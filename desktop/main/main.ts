import { app, BrowserWindow, dialog, ipcMain, Menu, MenuItem } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { RuntimeClient } from "./runtime-client.js";
import type {
  ApprovalDecision,
  ApprovalRequest,
  RuntimeNotification,
} from "./runtime-client.js";
import type { RuntimeStatus } from "../shared/ipc-contracts.js";
import { IPC, VALID_MODEL_IDS } from "../shared/ipc-contracts.js";


// ---------------------------------------------------------------------------
// Logging helpers — never log API keys, full prompts, or sensitive env vars
// ---------------------------------------------------------------------------

function log(level: "info" | "warn" | "error", context: string, message: string, meta?: Record<string, unknown>): void {
  const ts = new Date().toISOString();
  const entry = { ts, level, ctx: context, msg: message, ...meta };
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
let isQuitting = false;
let quitCanContinue = false;
let shutdownStarted = false;
let isShowingQuitDialog = false;

const hasSingleInstanceLock = app.requestSingleInstanceLock();

const pendingApprovals = new Map<
  string,
  { request: ApprovalRequest; resolve: (decision: ApprovalDecision) => void }
>();

/**
 * Projection of active (non-terminal) runs known to Main.
 * Updated by run/started, run/updated, run/completed notifications.
 * Terminal-state runs are removed immediately.
 * Only fields needed for quit-dialog display are stored; no user input.
 */
const activeRunProjection = new Map<string, {
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
      activeRunProjection.delete(run.id);
    } else {
      activeRunProjection.set(run.id, {
        sessionId: run.sessionId,
        status: run.status,
      });
    }
  }
}

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
// Window creation
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

// ---------------------------------------------------------------------------
// Application Menu with real keyboard shortcuts
// ---------------------------------------------------------------------------

function buildMenu(): void {
  const isMac = process.platform === "darwin";

  /**
   * Sends a shortcut event to the focused renderer window.
   * No-ops if no window is focused or if a modal-equivalent overlay is
   * present (checked via the Renderer's own guard logic).
   */
  function dispatchShortcut(channel: string): void {
    const focused = BrowserWindow.getFocusedWindow();
    if (focused) {
      focused.webContents.send(channel);
    } else {
      // No focused window — create or activate one
      const existing = BrowserWindow.getAllWindows()[0];
      if (existing) {
        existing.show();
        existing.focus();
      } else {
        createWindow();
      }
    }
  }

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
          click: () => dispatchShortcut(IPC.APP_NEW_TASK),
        },
        {
          label: "打开工作空间",
          accelerator: "CmdOrCtrl+O",
          click: () => dispatchShortcut(IPC.APP_OPEN_WORKSPACE),
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
      // Forward stderr but never log API keys or sensitive env vars
      console.error(`[runtime] ${line}`);
    },
  });
  runtimeClient = client;

  void client.waitForExit().then((code) => {
    if (!isQuitting && runtimeStatus.state !== "error") {
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
  } catch (error) {
    log("error", "runtime", "Runtime initialization failed", {
      message: error instanceof Error ? error.message : String(error),
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
    || !VALID_MODEL_IDS.has(String(modelId))
  ) {
    throw new Error("Run 参数无效。");
  }
  // Do NOT log userInput — may contain sensitive content
  return clientOrThrow().startRun(
    sessionId,
    userInput,
    modelId as "deepseek-v4-flash" | "deepseek-v4-pro",
  );
});
ipcMain.handle(IPC.RUN_CANCEL, (_event, runId: unknown) => {
  if (typeof runId !== "string") throw new Error("Run 参数无效。");
  log("info", "run", "Cancel requested", { runId });
  return clientOrThrow().cancelRun(runId);
});
ipcMain.handle(IPC.RUN_CONTINUE, (_event, runId: unknown, userInput: unknown) => {
  if (typeof runId !== "string" || typeof userInput !== "string") {
    throw new Error("Run 参数无效。");
  }
  return clientOrThrow().continueRun(runId, userInput);
});

ipcMain.handle(IPC.MODEL_STATUS, () => clientOrThrow().modelStatus());
ipcMain.handle(IPC.MODEL_LIST, () => clientOrThrow().listModels());
ipcMain.handle(IPC.MODEL_CONFIGURE, (_event, apiKey: unknown) => {
  if (typeof apiKey !== "string") throw new Error("API Key 参数无效。");
  // Never log the key itself
  return clientOrThrow().configureModel(apiKey);
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
    || (typeof feedback === "string" && Buffer.byteLength(feedback, "utf8") > 2_000)
    || (decision === "approve" && feedback !== undefined)
  ) {
    throw new Error("审批参数无效。");
  }
  const pending = pendingApprovals.get(id);
  if (!pending) return false;
  pendingApprovals.delete(id);
  // Do NOT log feedback content — may contain sensitive context
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

/**
 * Returns the number of currently active (non-terminal) runs tracked in
 * the Main process projection.
 */
function activeRunCount(): number {
  return activeRunProjection.size;
}

async function performShutdown(client: RuntimeClient): Promise<void> {
  const SHUTDOWN_TIMEOUT_MS = 8_000;
  log("info", "quit", "Beginning Runtime shutdown");
  const forceStop = new Promise<void>((resolve) => {
    setTimeout(() => {
      log("warn", "quit", "Shutdown timeout reached — forcing terminate");
      client.terminate();
      resolve();
    }, SHUTDOWN_TIMEOUT_MS);
  });
  const gracefulStop = client
    .shutdown()
    .then(() => client.waitForExit())
    .then(() => undefined)
    .catch((err: unknown) => {
      log("error", "quit", "Graceful shutdown failed", {
        message: err instanceof Error ? err.message : String(err),
      });
      client.terminate();
    });

  await Promise.race([gracefulStop, forceStop]);
  log("info", "quit", "Runtime shutdown complete");
}

app.on("before-quit", (event) => {
  // If already allowed to quit, do nothing
  if (!runtimeClient || quitCanContinue) return;

  event.preventDefault();
  isQuitting = true;

  // Prevent multiple quit dialogs from stacking
  if (shutdownStarted || isShowingQuitDialog) return;

  const hasActiveRuns = activeRunCount() > 0;

  if (!hasActiveRuns) {
    // No active runs — proceed directly to shutdown
    shutdownStarted = true;
    const client = runtimeClient;
    void performShutdown(client).finally(() => {
      quitCanContinue = true;
      app.quit();
    });
    return;
  }

  // Active runs exist — ask the user
  isShowingQuitDialog = true;
  const window = BrowserWindow.getAllWindows()[0];

  void dialog.showMessageBox(window ?? new BrowserWindow({ show: false }), {
    type: "question",
    buttons: ["返回 Eidos", "停止任务并退出"],
    defaultId: 0,
    cancelId: 0,
    title: "退出 Eidos",
    message: "当前有任务正在执行",
    detail: `有 ${activeRunCount()} 个活动任务正在运行。\n退出前将请求取消这些任务，数据可能不完整。`,
  }).then(({ response }) => {
    isShowingQuitDialog = false;

    if (response === 0) {
      // "返回 Eidos" — cancel quit
      log("info", "quit", "User chose to return to Eidos");
      isQuitting = false;
    } else {
      // "停止任务并退出"
      log("info", "quit", "User confirmed quit with active runs", {
        activeRunCount: activeRunCount(),
      });
      shutdownStarted = true;
      const client = runtimeClient;
      if (!client) {
        quitCanContinue = true;
        app.quit();
        return;
      }

      // Request cancel for all tracked active runs (best-effort)
      const cancelPromises = Array.from(activeRunProjection.keys()).map((runId) =>
        client.cancelRun(runId).catch((err: unknown) => {
          log("warn", "quit", "Failed to cancel run before quit", {
            runId,
            message: err instanceof Error ? err.message : String(err),
          });
        }),
      );

      void Promise.allSettled(cancelPromises).then(() =>
        performShutdown(client),
      ).finally(() => {
        quitCanContinue = true;
        app.quit();
      });
    }
  }).catch((err: unknown) => {
    isShowingQuitDialog = false;
    log("error", "quit", "Quit dialog error", {
      message: err instanceof Error ? err.message : String(err),
    });
    // On dialog error, default to safe: don't quit
    isQuitting = false;
  });
});

app.on("will-quit", () => runtimeClient?.terminate());

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
