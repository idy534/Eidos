import { app, BrowserWindow, dialog, ipcMain, Menu } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { RuntimeClient } from "./runtime-client.js";
import { redactLogLine, sanitizeLogValue } from "./log-redaction.js";
import { dispatchAppCommand as dispatchCommand, ensureAppWindow as ensureWindow } from "./app-command-dispatch.js";
import { QuitFlowController, type ActiveRunProjection, type QuitFlowDependencies } from "./quit-flow.js";
import { shutdownRuntime } from "./runtime-shutdown.js";
import type {
  ApprovalDecision,
  ApprovalRequest,
  AppShortcut,
  RuntimeNotification,
  RuntimeStatus,
  ModelCreateInput,
  ModelUpdateInput,
} from "../shared/index.js";
import { IPC, MAX_APPROVAL_FEEDBACK_BYTES } from "../shared/index.js";

// ---------------------------------------------------------------------------
// Logging helpers — never log API keys, full prompts, or sensitive env vars
// ---------------------------------------------------------------------------

function log(level: "info" | "warn" | "error", context: string, message: string, meta?: Record<string, unknown>): void {
  const ts = new Date().toISOString();
  const entry = {
    ts,
    level,
    ctx: context,
    msg: redactLogLine(message),
    ...(meta ? (sanitizeLogValue(meta) as Record<string, unknown>) : {}),
  };
  if (level === "error") {
    console.error(JSON.stringify(entry));
  } else {
    console.log(JSON.stringify(entry));
  }
}

// ---------------------------------------------------------------------------
// App-level state
// ---------------------------------------------------------------------------

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
app.setName("Eidos");

let runtimeStatus: RuntimeStatus = { state: "starting" };
let runtimeClient: RuntimeClient | undefined;
let runtimeTerminated = false;

function terminateRuntimeOnce(): void {
  if (runtimeTerminated) return;
  runtimeTerminated = true;
  runtimeClient?.terminate();
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();

const pendingApprovals = new Map<
  string,
  { request: ApprovalRequest; resolve: (decision: ApprovalDecision) => void }
>();

/**
 * Projection of active (non-terminal) runs known to Main.
 */
const activeRunProjectionMap = new Map<string, {
  sessionId: string;
  status: string;
  title?: string;
}>();

const TERMINAL_RUN_STATUSES = new Set([
  "stopped", "succeeded", "failed", "canceled", "interrupted",
]);

function updateActiveRunProjection(notification: RuntimeNotification): void {
  if (
    notification.method === "run/started"
    || notification.method === "run/updated"
    || notification.method === "run/completed"
  ) {
    const { run } = notification.params;
    if (TERMINAL_RUN_STATUSES.has(run.status)) {
      activeRunProjectionMap.delete(run.id);
    } else {
      activeRunProjectionMap.set(run.id, {
        sessionId: run.sessionId,
        status: run.status,
      });
    }
  }
}

const activeRunProjection: ActiveRunProjection = {
  runIds: () => Array.from(activeRunProjectionMap.keys()),
  count: () => activeRunProjectionMap.size,
};

const quitFlowDeps: QuitFlowDependencies = {
  hasRuntimeClient: () => Boolean(runtimeClient),
  showQuitDialog: async (activeCount) => {
    const window = BrowserWindow.getAllWindows()[0];
    const options: Electron.MessageBoxOptions = {
      type: "question",
      buttons: ["返回 Eidos", "停止任务并退出"],
      defaultId: 0,
      cancelId: 0,
      title: "退出 Eidos",
      message: "当前有任务正在执行",
      detail: `有 ${activeCount} 个活动任务正在运行。\n退出前将请求取消这些任务，数据可能不完整。`,
    };
    const { response } = window
      ? await dialog.showMessageBox(window, options)
      : await dialog.showMessageBox(options);
    return response === 1 ? "stop_and_exit" : "return_to_eidos";
  },
  cancelRun: async (runId) => {
    if (runtimeClient) {
      log("info", "quit", "Canceling run before quit", { runId });
      await runtimeClient.cancelRun(runId);
    }
  },
  shutdownRuntime: async () => {
    if (runtimeClient) {
      const client = runtimeClient;
      const shutdownClient = {
        shutdown: () => client.shutdown(),
        waitForExit: () => client.waitForExit(),
        terminate: terminateRuntimeOnce,
      };
      await shutdownRuntime(shutdownClient, {
        onDiagnostic: (level, message) => log(level, "quit", message),
      });
    }
  },
  requestFinalQuit: () => {
    app.quit();
  },
  log: (level, message, meta) => log(level, "quit", message, meta),
};

const quitFlowController = new QuitFlowController(activeRunProjection, quitFlowDeps);

// ---------------------------------------------------------------------------
// IPC helpers
// ---------------------------------------------------------------------------

function publishStatus(status: RuntimeStatus): void {
  runtimeStatus = status;
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send(IPC.RUNTIME_STATUS_EVENT, status);
  }
}

function publishNotification(notification: RuntimeNotification): void {
  updateActiveRunProjection(notification);
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send(IPC.RUNTIME_NOTIFICATION_EVENT, notification);
  }
  if (notification.method === "run/completed") {
    for (const [id, pending] of pendingApprovals) {
      if (pending.request.runId === notification.params.run.id) {
        pendingApprovals.delete(id);
        pending.resolve({ decision: "reject" });
      }
    }
  }
}

function requestApproval(request: ApprovalRequest): Promise<ApprovalDecision> {
  return new Promise((resolve) => {
    pendingApprovals.set(request.id, { request, resolve });
    for (const window of BrowserWindow.getAllWindows()) {
      window.webContents.send(IPC.APPROVAL_REQUESTED_EVENT, request);
    }
  });
}

function clientOrThrow(): RuntimeClient {
  if (!runtimeClient || runtimeStatus.state !== "ready") {
    throw new Error("Runtime 尚未就绪。");
  }
  return runtimeClient;
}

// ---------------------------------------------------------------------------
// Window creation & command dispatch
// ---------------------------------------------------------------------------

function createWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1180,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    show: false,
    backgroundColor: "#f4f2ed",
    title: "Eidos",
    webPreferences: {
      preload: path.join(currentDirectory, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  window.once("ready-to-show", () => window.show());
  void window.loadFile(path.join(currentDirectory, "../renderer/index.html"));
  return window;
}

const windowDeps = {
  getExistingWindow: () => BrowserWindow.getAllWindows()[0],
  createWindow,
};

export function ensureMainWindow(): BrowserWindow {
  return ensureWindow(windowDeps) as BrowserWindow;
}

export async function dispatchAppCommand(command: AppShortcut): Promise<void> {
  await dispatchCommand(command, windowDeps);
}

// ---------------------------------------------------------------------------
// Application Menu with real keyboard shortcuts
// ---------------------------------------------------------------------------

function buildMenu(): void {
  const isMac = process.platform === "darwin";

  const template: Electron.MenuItemConstructorOptions[] = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: "about" as const },
        { type: "separator" as const },
        { role: "services" as const },
        { type: "separator" as const },
        { role: "hide" as const },
        { role: "hideOthers" as const },
        { role: "unhide" as const },
        { type: "separator" as const },
        { role: "quit" as const },
      ],
    }] : []),
    {
      label: "文件",
      submenu: [
        {
          label: "新建任务",
          accelerator: "CmdOrCtrl+N",
          click: () => { void dispatchAppCommand(IPC.APP_NEW_TASK); },
        },
        {
          label: "打开工作空间...",
          accelerator: "CmdOrCtrl+O",
          click: () => { void dispatchAppCommand(IPC.APP_OPEN_WORKSPACE); },
        },
        { type: "separator" },
        isMac ? { role: "close" as const } : { role: "quit" as const },
      ],
    },
    {
      label: "编辑",
      submenu: [
        { role: "undo" as const },
        { role: "redo" as const },
        { type: "separator" as const },
        { role: "cut" as const },
        { role: "copy" as const },
        { role: "paste" as const },
        ...(isMac ? [
          { role: "pasteAndMatchStyle" as const },
          { role: "delete" as const },
          { role: "selectAll" as const },
        ] : [
          { role: "delete" as const },
          { type: "separator" as const },
          { role: "selectAll" as const },
        ]),
      ],
    },
    {
      label: "视图",
      submenu: [
        { role: "reload" as const },
        { role: "forceReload" as const },
        { role: "toggleDevTools" as const },
        { type: "separator" as const },
        { role: "resetZoom" as const },
        { role: "zoomIn" as const },
        { role: "zoomOut" as const },
        { type: "separator" as const },
        { role: "togglefullscreen" as const },
      ],
    },
    {
      label: "窗口",
      submenu: [
        { role: "minimize" as const },
        { role: "zoom" as const },
        ...(isMac ? [
          { type: "separator" as const },
          { role: "front" as const },
        ] : [
          { role: "close" as const },
        ]),
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

// ---------------------------------------------------------------------------
// Runtime startup
// ---------------------------------------------------------------------------

async function startRuntime(): Promise<void> {
  const runtimeRoot = path.join(app.getAppPath(), "runtime");
  const client = new RuntimeClient({
    pythonExecutable: process.env.EIDOS_PYTHON
      ?? path.join(app.getAppPath(), ".venv", "bin", "python"),
    runtimeRoot,
    dataDirectory: process.env.EIDOS_DATA_DIR ?? path.join(app.getPath("home"), ".eidos"),
    onNotification: publishNotification,
    onApprovalRequest: requestApproval,
    onStderr: (line) => {
      console.error(`[runtime] ${redactLogLine(line)}`);
    },
  });
  runtimeClient = client;

  void client.waitForExit().then((code) => {
    if (!quitFlowController.getState().isQuitting && runtimeStatus.state !== "error") {
      log("error", "runtime", "Runtime exited unexpectedly", { code });
      publishStatus({
        state: "error",
        message: `Runtime exited unexpectedly (code ${code}).`,
      });
    }
  });

  try {
    const initialized = await client.initialize();
    const storageHealth = await client.health();
    log("info", "runtime", "Runtime initialized", {
      protocolVersion: initialized.protocolVersion,
      runtimeVersion: initialized.runtimeVersion,
      storageState: storageHealth.state,
    });
    publishStatus({
      state: "ready",
      protocolVersion: initialized.protocolVersion,
      runtimeVersion: initialized.runtimeVersion,
      runShell: initialized.capabilities.runShell,
      modelConfigured: initialized.capabilities.modelConfigured,
      storageHealth,
    });
    if (process.env.EIDOS_ELECTRON_SMOKE === "1") {
      app.quit();
    }
  } catch (error) {
    const rawMessage = error instanceof Error ? error.message : String(error);
    const safeMessage = redactLogLine(rawMessage);
    log("error", "runtime", "Runtime initialization failed", {
      message: safeMessage,
    });
    publishStatus({
      state: "error",
      message: "Python Runtime 无法启动，请查看启动终端中的诊断日志。",
    });
  }
}

// ---------------------------------------------------------------------------
// IPC Handlers
// ---------------------------------------------------------------------------

ipcMain.handle(IPC.RUNTIME_GET_STATUS, () => runtimeStatus);
ipcMain.handle(IPC.RUNTIME_HEALTH, () => clientOrThrow().health());

ipcMain.handle(IPC.WORKSPACE_SELECT, async () => {
  const result = await dialog.showOpenDialog({
    title: "选择 Eidos Workspace",
    properties: ["openDirectory"],
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

ipcMain.handle(IPC.SESSION_LIST, () => clientOrThrow().listSessions());
ipcMain.handle(IPC.SESSION_READ, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  return clientOrThrow().readSession(sessionId);
});
ipcMain.handle(IPC.EVENT_LIST, (_event, sessionId: unknown, afterEventId: unknown) => {
  if (typeof sessionId !== "string" || typeof afterEventId !== "number") {
    throw new Error("Event 参数无效。");
  }
  return clientOrThrow().listEvents(sessionId, afterEventId);
});
ipcMain.handle(IPC.SESSION_CREATE, (_event, workspaceRoot: unknown) => {
  if (typeof workspaceRoot !== "string") throw new Error("Workspace 参数无效。");
  return clientOrThrow().createSession(workspaceRoot);
});
ipcMain.handle(IPC.SESSION_RENAME, (_event, sessionId: unknown, title: unknown) => {
  if (typeof sessionId !== "string" || typeof title !== "string") {
    throw new Error("Session 参数无效。");
  }
  return clientOrThrow().renameSession(sessionId, title);
});
ipcMain.handle(IPC.SESSION_DELETE, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  return clientOrThrow().deleteSession(sessionId);
});

ipcMain.handle(IPC.RUN_START, (_event, sessionId: unknown, userInput: unknown, modelId: unknown) => {
  if (
    typeof sessionId !== "string"
    || typeof userInput !== "string"
    || typeof modelId !== "string"
    || modelId.length === 0
    || modelId.length > 256
  ) {
    throw new Error("Run 参数无效。");
  }
  return clientOrThrow().startRun(
    sessionId,
    userInput,
    modelId,
  );
});
ipcMain.handle(IPC.RUN_CANCEL, (_event, runId: unknown) => {
  if (typeof runId !== "string") throw new Error("Run 参数无效。");
  log("info", "run", "Cancel requested", { runId });
  return clientOrThrow().cancelRun(runId);
});

ipcMain.handle(IPC.MODEL_PRESETS, () => clientOrThrow().listModelPresets());
ipcMain.handle(IPC.MODEL_LIST, () => clientOrThrow().listModels());
ipcMain.handle(IPC.MODEL_CREATE, (_event, input: unknown) => {
  if (!input || typeof input !== "object") throw new Error("模型参数无效。");
  return clientOrThrow().createModel(input as ModelCreateInput);
});
ipcMain.handle(IPC.MODEL_UPDATE, (_event, input: unknown) => {
  if (!input || typeof input !== "object") throw new Error("模型参数无效。");
  return clientOrThrow().updateModel(input as ModelUpdateInput);
});
ipcMain.handle(IPC.MODEL_DELETE, (_event, id: unknown) => {
  if (typeof id !== "string") throw new Error("模型参数无效。");
  return clientOrThrow().deleteModel(id);
});
ipcMain.handle(IPC.PLUGIN_LIST, () => clientOrThrow().listPlugins());
ipcMain.handle(IPC.PLUGIN_IMPORT, async () => {
  const result = await dialog.showOpenDialog({
    title: "导入本地 Eidos Plugin",
    properties: ["openDirectory"],
  });
  const sourcePath = result.canceled ? undefined : result.filePaths[0];
  return sourcePath ? clientOrThrow().importPlugin(sourcePath) : null;
});
ipcMain.handle(IPC.PLUGIN_SET_ENABLED, (_event, pluginId: unknown, enabled: unknown) => {
  if (typeof pluginId !== "string" || typeof enabled !== "boolean") {
    throw new Error("Plugin 参数无效。");
  }
  return clientOrThrow().setPluginEnabled(pluginId, enabled);
});
ipcMain.handle(IPC.PLUGIN_REMOVE, (_event, pluginId: unknown) => {
  if (typeof pluginId !== "string") throw new Error("Plugin 参数无效。");
  return clientOrThrow().removePlugin(pluginId);
});

ipcMain.handle(IPC.SKILL_LIST, () => clientOrThrow().listSkills());
ipcMain.handle(IPC.MCP_LIST, () => clientOrThrow().listMcpServers());
ipcMain.handle(IPC.EXTENSION_READ, () => clientOrThrow().readExtensions());
ipcMain.handle(IPC.EXTENSION_READ_EVENTS, (_event, afterEventId: unknown) => {
  if (typeof afterEventId !== "number") throw new Error("Extension Event 参数无效。");
  return clientOrThrow().readExtensionEvents(afterEventId);
});
ipcMain.handle(IPC.MCP_SET_ENABLED, (_event, pluginId: unknown, serverId: unknown, enabled: unknown) => {
  if (typeof pluginId !== "string" || typeof serverId !== "string" || typeof enabled !== "boolean") {
    throw new Error("MCP Server 参数无效。");
  }
  return clientOrThrow().setMcpEnabled(pluginId, serverId, enabled);
});

ipcMain.handle(IPC.APPROVAL_LIST, () =>
  Array.from(pendingApprovals.values(), ({ request }) => request),
);
ipcMain.handle(IPC.APPROVAL_RESPOND, (_event, id: unknown, decision: unknown, feedback: unknown) => {
  if (
    typeof id !== "string"
    || !["approve", "reject"].includes(String(decision))
    || (feedback !== undefined && typeof feedback !== "string")
    || (typeof feedback === "string" && Buffer.byteLength(feedback, "utf8") > MAX_APPROVAL_FEEDBACK_BYTES)
    || (decision === "approve" && feedback !== undefined)
  ) {
    throw new Error("审批参数无效。");
  }
  const pending = pendingApprovals.get(id);
  if (!pending) return false;
  pendingApprovals.delete(id);
  log("info", "approval", "Approval responded", { id, decision });
  const result: ApprovalDecision = decision === "approve"
    ? { decision: "approve" }
    : feedback
      ? { decision: "reject", feedback }
      : { decision: "reject" };
  pending.resolve(result);
  return true;
});

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const window = BrowserWindow.getAllWindows()[0];
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  });

  app.whenReady().then(() => {
    buildMenu();
    createWindow();
    void startRuntime();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Graceful quit with active-run awareness
// ---------------------------------------------------------------------------

app.on("before-quit", (event) => {
  quitFlowController.handleBeforeQuit(event);
});

app.on("will-quit", () => terminateRuntimeOnce());

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
