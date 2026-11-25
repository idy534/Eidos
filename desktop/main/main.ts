import { isQoderConnectionRequest } from "../shared/qoder.js";
import { validateSessionCreateOptions } from "./session-create-options.js";
import { isGitWorkspaceRequest, isProjectCreateFoldersRequest, isProjectGitContextBinding, isProjectReadRequest, isProjectUpdateRequest } from "../shared/project-workspaces.js";
import { isShellEnvironmentReadRequest, isShellEnvironmentRefreshRequest, isShellEnvironmentSaveRequest } from "../shared/shell-environment.js";
import { isGuardrailsReadRequest, isGuardrailsSaveRequest } from "../shared/guardrails.js";
import { isPersonalizationReadRequest, isPersonalizationSaveRequest } from "../shared/personalization.js";
import { isHookConfigurationRequest, isHookHistoryRequest } from "../shared/hooks.js";
import { webSearchOfficialUrl } from "./web-search-links.js";
import { isWebSearchReadRequest, isWebSearchSaveRequest } from "../shared/web-search.js";
import type { CustomProviderRequest } from "../shared/custom-models.js";
import { isChannelSaveRequest, isChannelReadRequest } from "../shared/channels.js";
import { isSessionForkRequest } from "../shared/session-fork.js";
import { isMemoryMethod, isMemoryRequest } from "../shared/memory.js";
import { exportMemoryNotes } from "./memory-export.js";
import { isAnswerInputRequest, isPlanEditRequest, isRunPlanningOptions } from "../shared/planning.js";
import { isSearchRequest, isReadAroundRequest, isFileFindRequest } from "../shared/search.js";
import { isSidebarSetPinnedRequest } from "../shared/sidebar.js";
import { isQueuedRunSteerRequest, isQueuedRunDeleteRequest, isQueuedRunUpdateRequest, isQueuedRunListRequest } from "../shared/queued-runs.js";
import type { RunPlanningOptions } from "../shared/planning.generated.js";
import { app, BrowserWindow, clipboard, crashReporter, dialog, ipcMain, Menu, nativeImage, Notification, powerMonitor, screen, shell as electronShell, Tray } from "electron";
import { existsSync } from "node:fs";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { realpath, stat, mkdtemp, writeFile, rm } from "node:fs/promises";
import { clipboardFilePaths, MAX_PASTED_IMAGE_BYTES, readPastedImageFile, windowsClipboardFilePaths } from "./clipboard-image.js";
import os from "node:os";
import { isInputPrepareRequest, isInputDraft } from "../shared/input-context.js";
import { isAgentDefinitionSaveRequest, isAgentDefinitionRemoveRequest } from "../shared/agent-definitions.js";
import path from "node:path";
import { promisify } from "node:util";
import * as nodePty from "node-pty";

import { buildRuntimeEnvironment, RuntimeClient } from "./runtime-client.js";
import { clearRuntimeState } from "./runtime-process-state.js";
import { exportDiagnosticBundle, RotatingFileLogger } from "./app-logger.js";
import { restoreDataBackup } from "./data-restore.js";
import { isMcpToolsRequest } from "../shared/mcp-details.js";
import {
  PLAYWRIGHT_EXTENSION_STORE_URL, isBrowserExtensionTokenRequest, isBrowserGrantRevokeRequest, isBrowserStatusRequest,
  isBrowserTargetRequest,
} from "../shared/browser.js";
import { isBuiltinPluginRequest, isMcpConnectionRequest } from "../shared/mcp-connections.js";
import { isAutomationRequest, isAutomationHistoryRequest, isAutomationRunNowRequest } from "../shared/automation.js";
import { isGoalRequest } from "../shared/goals.js";
import { isTeamMethod, isTeamRequest } from "../shared/teams.js";
import { resolveRuntimePaths } from "./runtime-paths.js";
import "./response-runtime-client.js";
import { redactLogLine, sanitizeLogValue } from "./log-redaction.js";
import { dispatchAppCommand as dispatchCommand } from "./app-command-dispatch.js";
import {
  guardIpcHandlerForQuit,
  QuitFlowController,
  type ActiveRunProjection,
  type QuitFlowDependencies,
} from "./quit-flow.js";
import { shutdownRuntime } from "./runtime-shutdown.js";
import { resolveWorkspaceFileForOpen } from "./workspace-open.js";
import { ArtifactPreviewManager } from "./artifact-preview.js";
import { workspacePreviewUrl } from "./workspace-preview.js";
import { TerminalManager } from "./terminal-manager.js";
import {
  buildApprovalNotification,
  buildRunCompletedNotification,
  buildViewMenuSubmenu,
  loadWindowState,
  saveWindowStateSync,
  shouldHideWindowToTray,
} from "./desktop-shell.js";
import type {
  ApprovalMode,
  ApprovalDecision,
  ApprovalRequest,
  AppShortcut,
  RuntimeNotification,
  RuntimeStatus,
  ModelCreateInput,
  ModelReasoningSelection,
  ModelUpdateInput,
  McpCreateInput,
  McpUpdateInput,
  ResponseFeedbackValue,
  ReviewCommentCreateInput,
} from "../shared/index.js";
import { IPC, MAX_APPROVAL_FEEDBACK_BYTES } from "../shared/index.js";
import { isAgentChangesRequest, isAgentMessageFromUserRequest } from "../shared/collaboration.js";

// ---------------------------------------------------------------------------
// Logging helpers — never log API keys, full prompts, or sensitive env vars
// ---------------------------------------------------------------------------

const execFileAsync = promisify(execFile);
let appLogger: RotatingFileLogger | undefined;

function getAppLogger(): RotatingFileLogger {
  appLogger ??= new RotatingFileLogger(path.join(runtimeDataDirectory(), "logs"));
  return appLogger;
}

function log(level: "info" | "warn" | "error", context: string, message: string, meta?: Record<string, unknown>): void {
  const ts = new Date().toISOString();
  const entry = {
    ts,
    level,
    ctx: context,
    msg: redactLogLine(message),
    ...(meta ? (sanitizeLogValue(meta) as Record<string, unknown>) : {}),
  };
  const serialized = JSON.stringify(entry);
  getAppLogger().append("desktop.log", serialized);
  if (level === "error") {
    console.error(serialized);
  } else {
    console.log(serialized);
  }
}

// ---------------------------------------------------------------------------
// App-level state
// ---------------------------------------------------------------------------

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
app.setName("Eidos");

const artifactPreview = new ArtifactPreviewManager(() => clientOrThrow());

let runtimeStatus: RuntimeStatus = { state: "starting" };
let runtimeClient: RuntimeClient | undefined;
let runtimeTerminated = false;
let runtimeStartGeneration = 0;
let runtimeRestartPromise: Promise<RuntimeStatus> | undefined;

function terminateRuntimeOnce(): void {
  if (runtimeTerminated) return;
  runtimeTerminated = true;
  runtimeClient?.terminate();
}

function clearRuntimeProcessState(): void {
  clearRuntimeState(activeRunProjectionMap, pendingApprovals);
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
  onQuitCommitted: () => {
    // Drop UI-side pending approval entries without sending synthetic reject
    // decisions; Runtime's run/cancel and supervisor.shutdown settle them as canceled.
    pendingApprovals.clear();
    destroySystemTray();
    artifactPreview.closeAll();
    terminalManager.closeAll();
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed()) {
        window.destroy();
      }
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
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
      window.webContents.send(IPC.RUNTIME_STATUS_EVENT, status);
    }
  }
}

function publishNotification(notification: RuntimeNotification): void {
  updateActiveRunProjection(notification);
  if (!quitFlowController.getState().quitCommitted) {
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
        window.webContents.send(IPC.RUNTIME_NOTIFICATION_EVENT, notification);
      }
    }
  }
  if (notification.method === "run/completed") {
    for (const [id, pending] of pendingApprovals) {
      if (pending.request.runId === notification.params.run.id) {
        pendingApprovals.delete(id);
        pending.resolve({ decision: "reject" });
      }
    }
    if (!quitFlowController.getState().quitCommitted && isMainWindowUnfocused() && Notification.isSupported()) {
      const payload = buildRunCompletedNotification(notification.params.run.status);
      if (payload) {
        const nativeNotice = new Notification(payload);
        nativeNotice.on("click", showMainWindow);
        nativeNotice.show();
      }
    }
  }
}

function requestApproval(request: ApprovalRequest): Promise<ApprovalDecision> {
  if (quitFlowController.getState().quitCommitted) {
    // Leave unsettled so Runtime cancels the pending approval on quit instead of recording a user rejection.
    return new Promise<ApprovalDecision>(() => {});
  }
  return new Promise((resolve) => {
    pendingApprovals.set(request.id, { request, resolve });
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
        window.webContents.send(IPC.APPROVAL_REQUESTED_EVENT, request);
      }
    }
    if (isMainWindowUnfocused() && Notification.isSupported()) {
      const nativeNotice = new Notification(buildApprovalNotification(request));
      nativeNotice.on("click", showMainWindow);
      nativeNotice.show();
    }
  });
}

function clientOrThrow(): RuntimeClient {
  if (!runtimeClient || runtimeStatus.state !== "ready") {
    throw new Error("Runtime 尚未就绪。");
  }
  return runtimeClient;
}

const terminalManager = new TerminalManager({
  readSession: (sessionId) => clientOrThrow().readSession(sessionId),
  resolveProjectRoot: async (projectId) => {
    const project = (await clientOrThrow().listProjects()).items.find((item) => item.id === projectId);
    if (!project) throw new Error("Project 当前不可用。");
    return project.workspaceRoot;
  },
  spawn: (file, args, options) => nodePty.spawn(file, args, options),
});

function isNonEmptyStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value)
    && value.length > 0
    && value.every((item) => typeof item === "string" && item.length > 0)
  );
}

function isReviewCommentCreateInput(value: unknown): value is ReviewCommentCreateInput {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return (
    Object.keys(record).every((key) => [
      "commentId", "path", "scope", "side", "line", "body", "baseHead", "diffHash",
    ].includes(key))
    && typeof record.commentId === "string"
    && typeof record.path === "string"
    && (record.scope === "head" || record.scope === "baseline")
    && (record.side === "old" || record.side === "new")
    && Number.isInteger(record.line)
    && Number(record.line) > 0
    && typeof record.body === "string"
    && typeof record.baseHead === "string"
    && typeof record.diffHash === "string"
  );
}

// ---------------------------------------------------------------------------
// Window creation & command dispatch
// ---------------------------------------------------------------------------

let systemTray: Tray | null = null;

function resolveTrayImage(): Electron.NativeImage {
  const candidates = [
    path.join(process.resourcesPath ?? "", "eidos-app-icon.png"),
    path.join(currentDirectory, "../../assets/branding/eidos-logo/preview/eidos-app-icon.png"),
    path.join(app.getAppPath(), "assets/branding/eidos-logo/preview/eidos-app-icon.png"),
  ];
  for (const candidate of candidates) {
    if (candidate && existsSync(candidate)) {
      const image = nativeImage.createFromPath(candidate);
      if (!image.isEmpty()) {
        return image.resize({ width: 16, height: 16 });
      }
    }
  }
  return nativeImage.createEmpty();
}

function isMainWindowUnfocused(): boolean {
  const window = BrowserWindow.getAllWindows()[0];
  return !window || window.isDestroyed() || !window.isVisible() || window.isMinimized() || !window.isFocused();
}

function showMainWindow(): void {
  if (quitFlowController.getState().quitCommitted) return;
  const existing = BrowserWindow.getAllWindows()[0];
  if (!existing || existing.isDestroyed()) {
    const created = createWindow();
    created.focus();
    return;
  }
  if (existing.isMinimized()) existing.restore();
  existing.show();
  existing.focus();
}

function ensureSystemTray(): void {
  if (systemTray || process.env.EIDOS_DISABLE_TRAY === "1") return;
  try {
    const tray = new Tray(resolveTrayImage());
    tray.setToolTip("Eidos");
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "显示 Eidos", click: showMainWindow },
      { type: "separator" },
      { label: "退出 Eidos", click: () => app.quit() },
    ]));
    tray.on("click", showMainWindow);
    tray.on("double-click", showMainWindow);
    systemTray = tray;
  } catch (error) {
    log("warn", "tray", "Failed to initialize system tray", {
      message: error instanceof Error ? error.message : String(error),
    });
  }
}

function destroySystemTray(): void {
  if (systemTray && !systemTray.isDestroyed()) {
    systemTray.destroy();
  }
  systemTray = null;
}

function createWindow(): BrowserWindow {
  const isMac = process.platform === "darwin";
  const windowStatePath = path.join(runtimeDataDirectory(), "window-state.json");
  const initialBounds = loadWindowState(
    windowStatePath,
    screen.getAllDisplays().map((display) => display.workArea),
  );
  const window = new BrowserWindow({
    width: initialBounds.width,
    height: initialBounds.height,
    ...(initialBounds.x !== undefined && initialBounds.y !== undefined
      ? { x: initialBounds.x, y: initialBounds.y }
      : {}),
    minWidth: 800,
    minHeight: 600,
    show: false,
    title: "Eidos",
    ...(isMac
      ? {
          transparent: true,
          backgroundColor: "#00000000",
          vibrancy: "under-window" as const,
          visualEffectState: "active" as const,
          titleBarStyle: "hidden" as const,
          trafficLightPosition: { x: 16, y: 16 },
        }
      : {
          transparent: false,
          backgroundColor: "#f8f9fb",
          titleBarStyle: "default" as const,
          autoHideMenuBar: true,
        }),
    webPreferences: {
      preload: path.join(currentDirectory, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      plugins: true,
    },
  });
  if (!isMac) {
    window.setMenuBarVisibility(false);
  }

  window.webContents.on("context-menu", (_event, params) => {
    if (!params.isEditable && !params.selectionText) return;
    const template: Electron.MenuItemConstructorOptions[] = params.isEditable ? [
      { role: "undo", label: "撤销", enabled: params.editFlags.canUndo },
      { role: "redo", label: "重做", enabled: params.editFlags.canRedo },
      { type: "separator" },
      { role: "cut", label: "剪切", enabled: params.editFlags.canCut },
      { role: "copy", label: "复制", enabled: params.editFlags.canCopy },
      { role: "paste", label: "粘贴", enabled: params.editFlags.canPaste },
      { role: "pasteAndMatchStyle", label: "粘贴并匹配样式", enabled: params.editFlags.canPaste },
      { role: "selectAll", label: "全选" },
    ] : [{ role: "copy", label: "复制", enabled: params.editFlags.canCopy }];
    if (params.selectionText && !params.isEditable) template.push(
      { type: "separator" },
      { label: "引用到输入框", enabled: params.selectionText.length <= 65536,
        click: () => window.webContents.send(IPC.INPUT_QUOTE, params.selectionText) },
    );
    Menu.buildFromTemplate(template).popup({ window });
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  let saveBoundsTimer: ReturnType<typeof setTimeout> | undefined;
  const captureAndSaveWindowState = () => {
    if (window.isDestroyed()) return;
    const isMaximized = window.isMaximized();
    const bounds = isMaximized ? window.getNormalBounds() : window.getBounds();
    saveWindowStateSync(windowStatePath, {
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: bounds.height,
      isMaximized,
    });
  };
  const scheduleSaveWindowState = () => {
    if (saveBoundsTimer) clearTimeout(saveBoundsTimer);
    saveBoundsTimer = setTimeout(captureAndSaveWindowState, 300);
  };
  window.on("resize", scheduleSaveWindowState);
  window.on("move", scheduleSaveWindowState);
  window.on("maximize", scheduleSaveWindowState);
  window.on("unmaximize", scheduleSaveWindowState);
  window.once("ready-to-show", () => {
    if (initialBounds.isMaximized) window.maximize();
    window.show();
  });
  window.on("close", (event) => {
    if (saveBoundsTimer) {
      clearTimeout(saveBoundsTimer);
      saveBoundsTimer = undefined;
    }
    captureAndSaveWindowState();
    if (shouldHideWindowToTray({
      platform: process.platform,
      hasTray: Boolean(systemTray),
      quitCommitted: quitFlowController.getState().quitCommitted,
      disableTrayEnv: process.env.EIDOS_DISABLE_TRAY === "1",
    })) {
      event.preventDefault();
      window.hide();
    }
  });
  const ownerId = window.webContents.id;
  const previewContents = window.webContents;
  window.on("closed", () => artifactPreview.closeOwner(previewContents));
  window.webContents.once("destroyed", () => terminalManager.closeOwner(ownerId));
  void window.loadFile(path.join(currentDirectory, "../renderer/index.html"));
  return window;
}

const windowDeps = {
  getExistingWindow: () => BrowserWindow.getAllWindows()[0],
  createWindow,
};

export async function dispatchAppCommand(command: AppShortcut): Promise<void> {
  if (quitFlowController.getState().quitCommitted) return;
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
      submenu: buildViewMenuSubmenu(app.isPackaged),
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
  const startGeneration = ++runtimeStartGeneration;
  let client: RuntimeClient | undefined;
  try {
    const runtimePaths = resolveRuntimePaths({
      isPackaged: app.isPackaged,
      appPath: app.getAppPath(),
      resourcesPath: process.resourcesPath,
      environment: process.env,
    });
    client = new RuntimeClient({
      pythonExecutable: runtimePaths.pythonExecutable,
      runtimeRoot: runtimePaths.runtimeRoot,
      dataDirectory: runtimeDataDirectory(),
      clientVersion: app.getVersion(),
      environmentPolicy: app.isPackaged ? "packaged" : "development",
      onNotification: publishNotification,
      onApprovalRequest: requestApproval,
      onStderr: (line) => {
        const clean = redactLogLine(line);
        getAppLogger().append("runtime.log", clean);
        console.error(`[runtime] ${clean}`);
      },
    });
    if (startGeneration !== runtimeStartGeneration) {
      client.terminate();
      return;
    }
    runtimeClient = client;

    void client.waitForExit().then((code) => {
      if (
        runtimeStartGeneration === startGeneration
        && runtimeClient === client
        && !quitFlowController.getState().isQuitting
        && runtimeStatus.state !== "error"
      ) {
        runtimeClient = undefined;
        clearRuntimeProcessState();
        log("error", "runtime", "Runtime exited unexpectedly", { code });
        publishStatus({
          state: "error",
          message: `Runtime exited unexpectedly (code ${code}).`,
        });
      }
    });

    const initialized = await client.initialize();
    const storageHealth = await client.health();
    if (quitFlowController.getState().quitCommitted) {
      return;
    }
    if (startGeneration !== runtimeStartGeneration || runtimeClient !== client) {
      client.terminate();
      return;
    }
    log("info", "runtime", "Runtime initialized", {
      protocolVersion: initialized.protocolVersion,
      runtimeVersion: initialized.runtimeVersion,
      storageState: storageHealth.state,
      ...(storageHealth.code ? { storageCode: storageHealth.code } : {}),
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
    if (quitFlowController.getState().quitCommitted) {
      return;
    }
    if (startGeneration !== runtimeStartGeneration) {
      client?.terminate();
      return;
    }
    if (runtimeClient === client) runtimeClient = undefined;
    client?.terminate();
    if (runtimeStatus.state === "error") return;
    const rawMessage = error instanceof Error ? error.message : String(error);
    const safeMessage = redactLogLine(rawMessage);
    log("error", "runtime", "Runtime initialization failed", {
      message: safeMessage,
    });
    publishStatus({
      state: "error",
      message: "Python Runtime 无法启动，请导出诊断包或查看本地数据目录下的 logs/ 日志文件。",
    });
  }
}

async function waitForRuntimeExit(client: RuntimeClient): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let settled = false;
  await new Promise<void>((resolve) => {
    const settle = (): void => {
      if (settled) return;
      settled = true;
      if (timer !== undefined) clearTimeout(timer);
      resolve();
    };
    timer = setTimeout(settle, 8_000);
    void client.waitForExit().then(settle, settle);
  });
}

function restartRuntime(): Promise<RuntimeStatus> {
  if (runtimeRestartPromise) return runtimeRestartPromise;

  const restart = (async (): Promise<RuntimeStatus> => {
    const previous = runtimeClient;
    runtimeClient = undefined;
    runtimeStartGeneration += 1;
    clearRuntimeProcessState();
    publishStatus({ state: "starting" });
    if (previous) {
      previous.terminate();
      await waitForRuntimeExit(previous);
    }
    await startRuntime();
    return runtimeStatus;
  })();
  runtimeRestartPromise = restart;
  const clearRestartPromise = (): void => {
    if (runtimeRestartPromise === restart) runtimeRestartPromise = undefined;
  };
  void restart.then(clearRestartPromise, clearRestartPromise);
  return restart;
}

// ---------------------------------------------------------------------------
// IPC Handlers
// ---------------------------------------------------------------------------

function handleIpc<Args extends unknown[], Result>(
  channel: string,
  handler: (event: Electron.IpcMainInvokeEvent, ...args: Args) => Result | Promise<Result>,
): void {
  ipcMain.handle(
    channel,
    guardIpcHandlerForQuit(() => quitFlowController.getState().quitCommitted, handler),
  );
}

handleIpc(IPC.RUNTIME_GET_STATUS, () => runtimeStatus);
handleIpc(IPC.RUNTIME_HEALTH, () => clientOrThrow().health());
handleIpc(IPC.RUNTIME_RESTART, () => restartRuntime());
handleIpc(IPC.DIAGNOSTICS_EXPORT, async (event) => {
  inputOwner(event);
  const owner = BrowserWindow.fromWebContents(event.sender);
  if (!owner) throw new Error("无法打开诊断包保存窗口。");
  const result = await dialog.showSaveDialog(owner, {
    title: "导出脱敏诊断包",
    defaultPath: `eidos-diagnostics-${app.getVersion()}.zip`,
    filters: [{ name: "ZIP", extensions: ["zip"] }],
  });
  if (result.canceled || !result.filePath) return false;
  const dataDir = runtimeDataDirectory();
  exportDiagnosticBundle({
    destinationPath: result.filePath,
    logDirectory: path.join(dataDir, "logs"),
    crashDumpsDirectory: path.join(dataDir, "crash-dumps"),
    summary: {
      appVersion: app.getVersion(),
      platform: process.platform,
      arch: process.arch,
      electronVersion: process.versions.electron,
      runtimeStatus,
    },
  });
  return true;
});

handleIpc(IPC.WORKSPACE_SELECT, async () => {
  const result = await dialog.showOpenDialog({
    title: "选择 Eidos Workspace",
    properties: ["openDirectory"],
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

handleIpc(IPC.PROJECT_LIST, () => clientOrThrow().listProjects());
handleIpc(IPC.PROJECT_CREATE_FOLDERS, (_event, request: unknown) => {
  if (!isProjectCreateFoldersRequest(request)) throw new Error("项目配置无效。");
  return clientOrThrow().createProjectFolders(request);
});
handleIpc(IPC.GIT_REPOSITORY_REQUEST, (_event, request: unknown) => {
  if (!isGitWorkspaceRequest(request)) throw new Error("Git 目标参数无效。");
  return clientOrThrow().repositoryGit(request);
});
handleIpc(IPC.PROJECT_READ, (_event, request: unknown) => {
  if (!isProjectReadRequest(request)) throw new Error("项目参数无效。");
  return clientOrThrow().readProject(request.projectId);
});
handleIpc(IPC.PROJECT_UPDATE, (_event, request: unknown) => {
  if (!isProjectUpdateRequest(request)) throw new Error("项目配置无效。");
  return clientOrThrow().updateProject(request);
});
handleIpc(IPC.PERSONALIZATION_READ, (_event, request: unknown) => {
  const value = request ?? {};
  if (!isPersonalizationReadRequest(value)) throw new Error("个性化读取参数无效。");
  return clientOrThrow().readPersonalization(value);
});
handleIpc(IPC.PERSONALIZATION_SAVE, (_event, request: unknown) => {
  if (!isPersonalizationSaveRequest(request)) throw new Error("个性化设置无效，请检查填写内容。");
  return clientOrThrow().savePersonalization(request);
});
handleIpc(IPC.SHELL_ENVIRONMENT_READ, (_event, request: unknown) => {
  const value = request ?? {};
  if (!isShellEnvironmentReadRequest(value)) throw new Error("沙盒环境读取参数无效。");
  return clientOrThrow().readShellEnvironment(value);
});
handleIpc(IPC.SHELL_ENVIRONMENT_SAVE, (_event, request: unknown) => {
  if (!isShellEnvironmentSaveRequest(request)) throw new Error("沙盒环境配置无效，请检查各项参数。");
  return clientOrThrow().saveShellEnvironment(request);
});
handleIpc(IPC.SHELL_ENVIRONMENT_REFRESH, (_event, request: unknown) => {
  const value = request ?? {};
  if (!isShellEnvironmentRefreshRequest(value)) throw new Error("沙盒环境刷新参数无效。");
  return clientOrThrow().refreshShellEnvironment(value);
});
handleIpc(IPC.GUARDRAILS_READ, (_event, request: unknown) => {
  const value = request ?? {};
  if (!isGuardrailsReadRequest(value)) throw new Error("后台护栏读取参数无效。");
  return clientOrThrow().readGuardrails(value);
});
handleIpc(IPC.GUARDRAILS_SAVE, (_event, request: unknown) => {
  if (!isGuardrailsSaveRequest(request)) throw new Error("后台护栏配置无效，请检查数值范围。");
  return clientOrThrow().saveGuardrails(request);
});

handleIpc(IPC.WEB_SEARCH_READ, (_event, request: unknown) => {
  const value = request ?? {};
  if (!isWebSearchReadRequest(value)) throw new Error("网页搜索读取参数无效。");
  return clientOrThrow().readWebSearch(value);
});
handleIpc(IPC.WEB_SEARCH_SAVE, (_event, request: unknown) => {
  if (!isWebSearchSaveRequest(request)) throw new Error("网页搜索配置无效，请检查各项参数。");
  return clientOrThrow().saveWebSearch(request);
});
handleIpc(IPC.WEB_SEARCH_OFFICIAL_PAGE, (event, request: unknown) => {
  previewOwner(event);
  return electronShell.openExternal(webSearchOfficialUrl(request));
});

handleIpc(IPC.CHANNEL_READ, (_event, request: unknown) => {
  const value = request === undefined ? {} : request;
  if (!isChannelReadRequest(value)) throw new Error("助理类型无效，请刷新后重试。");
  return clientOrThrow().readChannel(value);
});
handleIpc(IPC.CHANNEL_SAVE, (_event, request: unknown) => {
  if (!isChannelSaveRequest(request)) throw new Error("渠道配置无效，请检查必填项。 ");
  return clientOrThrow().saveChannel(request);
});
handleIpc(IPC.GOAL_EXECUTE, (_event, request: unknown) => {
  if (!isGoalRequest(request)) throw new Error("目标参数无效。");
  return clientOrThrow().goal(request);
});
handleIpc(IPC.TEAM_REQUEST, (event, method: unknown, request: unknown) => {
  inputOwner(event);
  if (!isTeamMethod(method) || !isTeamRequest(method, request)) throw new Error("团队请求无效。");
  return clientOrThrow().team(method, request);
});
handleIpc(IPC.AUTOMATION_EXECUTE, (_event, request: unknown) => {
  if (!isAutomationRequest(request)) throw new Error("定时任务参数无效。");
  return clientOrThrow().automation(request);
});
handleIpc(IPC.AUTOMATION_HISTORY, (_event, request: unknown) => {
  if (!isAutomationHistoryRequest(request)) throw new Error("定时任务历史参数无效。");
  return clientOrThrow().automationHistory(request);
});
handleIpc(IPC.AUTOMATION_RUN_NOW, (_event, request: unknown) => {
  if (!isAutomationRunNowRequest(request)) throw new Error("立即执行参数无效。");
  return clientOrThrow().automationRunNow(request);
});
handleIpc(IPC.WORKSPACE_READ_FOR_FIND, (_event, request: unknown) => {
  if (!isFileFindRequest(request)) throw new Error("文件查找参数无效");
  return clientOrThrow().readWorkspaceFileForFind(request);
});
handleIpc(IPC.SEARCH_SESSIONS, (_event, request: unknown) => {
  if (!isSearchRequest(request)) throw new Error("搜索参数无效");
  return clientOrThrow().searchSessions(request);
});
handleIpc(IPC.SESSION_READ_AROUND, (_event, request: unknown) => {
  if (!isReadAroundRequest(request)) throw new Error("消息定位参数无效");
  return clientOrThrow().readSessionAround(request);
});
handleIpc(IPC.SIDEBAR_LIST_PINS, () => clientOrThrow().listSidebarPins());
handleIpc(IPC.RUN_UPDATE_QUEUED, (_event, request: unknown) => {
  if (!isQueuedRunUpdateRequest(request)) throw new Error("排队消息参数无效。");
  return clientOrThrow().updateQueuedRun(request);
});
handleIpc(IPC.RUN_LIST_QUEUED, (_event, request: unknown) => {
  if (!isQueuedRunListRequest(request)) throw new Error("排队消息参数无效。");
  return clientOrThrow().listQueuedRuns(request);
});
handleIpc(IPC.RUN_STEER_QUEUED, (_event, request: unknown) => {
  if (!isQueuedRunSteerRequest(request)) throw new Error("引导消息参数无效");
  return clientOrThrow().steerQueuedRun(request);
});
handleIpc(IPC.RUN_DELETE_QUEUED, (_event, request: unknown) => {
  if (!isQueuedRunDeleteRequest(request)) throw new Error("排队消息参数无效。");
  return clientOrThrow().deleteQueuedRun(request);
});
handleIpc(IPC.SIDEBAR_SET_PINNED, (_event, request: unknown) => {
  if (!isSidebarSetPinnedRequest(request)) throw new Error("置顶参数无效。");
  return clientOrThrow().setSidebarPinned(request);
});
handleIpc(IPC.PROJECT_CREATE, (_event, name: unknown, workspaceRoot: unknown) => {
  if (
    (name !== undefined && (typeof name !== "string" || name.length > 120))
    || typeof workspaceRoot !== "string"
    || workspaceRoot.trim().length === 0
  ) {
    throw new Error("Project 参数无效。");
  }
  return clientOrThrow().createProject(
    typeof name === "string" ? name.trim() || undefined : undefined,
    workspaceRoot,
  );
});
handleIpc(IPC.PROJECT_DELETE, (_event, projectId: unknown) => {
  if (typeof projectId !== "string" || projectId.length === 0 || projectId.length > 256) {
    throw new Error("Project 参数无效。");
  }
  return clientOrThrow().deleteProject(projectId);
});
handleIpc(IPC.SESSION_LIST, (_event, cursor: unknown) => {
  if (cursor !== undefined && (typeof cursor !== "string" || !cursor || cursor.length > 512)) {
    throw new Error("分页参数无效。");
  }
  return clientOrThrow().listSessions(cursor === undefined ? {} : { cursor });
});
handleIpc(IPC.TOOL_TEXT_READ, (_event, sessionId: unknown, toolCallId: unknown, field: unknown, sha256: unknown, offset: unknown) => {
  if (typeof sessionId !== "string" || !sessionId || sessionId.length > 256
    || typeof toolCallId !== "string" || !toolCallId || toolCallId.length > 256
    || (field !== "diff" && field !== "result" && field !== "content")
    || typeof sha256 !== "string" || !/^[a-f0-9]{64}$/.test(sha256)
    || typeof offset !== "number" || !Number.isSafeInteger(offset) || offset < 0) {
    throw new Error("工具文本读取参数无效。");
  }
  return clientOrThrow().readToolText(sessionId, toolCallId, field, sha256, offset);
});
handleIpc(IPC.SESSION_READ, (_event, sessionId: unknown, options: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  if (options !== undefined && (!options || typeof options !== "object" || Object.keys(options).some((key) => !["itemLimit", "beforeItemId"].includes(key)))) throw new Error("分页参数无效。");
  const itemLimit = options ? Reflect.get(options, "itemLimit") : undefined;
  const beforeItemId = options ? Reflect.get(options, "beforeItemId") : undefined;
  if (itemLimit !== undefined && (!Number.isInteger(itemLimit) || itemLimit < 1 || itemLimit > 500) || beforeItemId !== undefined && (typeof beforeItemId !== "string" || beforeItemId.length > 256)) throw new Error("分页参数无效。");
  return clientOrThrow().readSession(sessionId, { ...(itemLimit === undefined ? {} : { itemLimit }), ...(beforeItemId === undefined ? {} : { beforeItemId }) });
});
handleIpc(IPC.EVENT_LIST, (_event, sessionId: unknown, afterEventId: unknown) => {
  if (typeof sessionId !== "string" || typeof afterEventId !== "number") {
    throw new Error("Event 参数无效。");
  }
  return clientOrThrow().listEvents(sessionId, afterEventId);
});
handleIpc(IPC.SESSION_CREATE, (_event, workspaceRoot: unknown, options: unknown) => {
  if (workspaceRoot !== null && typeof workspaceRoot !== "string") {
    throw new Error("Workspace 参数无效。");
  }
  return clientOrThrow().createSession(workspaceRoot, validateSessionCreateOptions(options));
});
handleIpc(IPC.SESSION_FORK, (_event, request: unknown) => {
  if (!isSessionForkRequest(request)) throw new Error("分叉参数无效。");
  return clientOrThrow().forkSession(request);
});
handleIpc(IPC.SESSION_CREATE_BRANCH, (_event, sessionId: unknown, branch: unknown) => {
  if (
    typeof sessionId !== "string"
    || typeof branch !== "string"
    || branch.length === 0
    || branch.length > 4096
  ) {
    throw new Error("Branch 参数无效。");
  }
  return clientOrThrow().createSessionBranch(sessionId, branch);
});
handleIpc(IPC.SESSION_HANDOFF, (_event, sessionId: unknown, target: unknown) => {
  if (
    typeof sessionId !== "string"
    || (target !== "local" && target !== "worktree")
  ) {
    throw new Error("Handoff 参数无效。");
  }
  artifactPreview.closeSession(sessionId);
  terminalManager.closeSession(sessionId);
  return clientOrThrow().handoffSession(sessionId, target).then((result) => {
    artifactPreview.closeSession(sessionId);
    terminalManager.closeSession(sessionId);
    return result;
  });
});
handleIpc(IPC.SESSION_RESTORE_WORKTREE, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  artifactPreview.closeSession(sessionId);
  terminalManager.closeSession(sessionId);
  return clientOrThrow().restoreSessionWorktree(sessionId).then((result) => {
    artifactPreview.closeSession(sessionId);
    terminalManager.closeSession(sessionId);
    return result;
  });
});
handleIpc(IPC.WORKTREE_SETTINGS_READ, () => clientOrThrow().readWorktreeSettings());
handleIpc(IPC.WORKTREE_SETTINGS_UPDATE, (_event, input: unknown) => {
  if (
    !input
    || typeof input !== "object"
    || typeof (input as { automaticCleanup?: unknown }).automaticCleanup !== "boolean"
    || !Number.isInteger((input as { managedWorktreeLimit?: unknown }).managedWorktreeLimit)
    || Number((input as { managedWorktreeLimit: number }).managedWorktreeLimit) < 1
    || Number((input as { managedWorktreeLimit: number }).managedWorktreeLimit) > 100
  ) {
    throw new Error("Worktree 设置参数无效。");
  }
  const settings = input as { automaticCleanup: boolean; managedWorktreeLimit: number };
  return clientOrThrow().updateWorktreeSettings(settings);
});
handleIpc(IPC.PROJECT_GIT_CONTEXT, (_event, target: unknown) => {
  if (typeof target !== "string" && !isProjectGitContextBinding(target)) {
    throw new Error("Git 目标参数无效。");
  }
  return clientOrThrow().readProjectGitContext(target);
});
handleIpc(IPC.SESSION_RENAME, (_event, sessionId: unknown, title: unknown) => {
  if (typeof sessionId !== "string" || typeof title !== "string") {
    throw new Error("Session 参数无效。");
  }
  return clientOrThrow().renameSession(sessionId, title);
});
handleIpc(IPC.SESSION_DELETE, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  return clientOrThrow().deleteSession(sessionId).then((result) => {
    artifactPreview.closeSession(sessionId);
    terminalManager.closeSession(sessionId);
    return result;
  });
});
handleIpc(IPC.SESSION_GIT_STATUS, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  return clientOrThrow().readSessionGitStatus(sessionId);
});
handleIpc(IPC.WORKSPACE_LIST_DIRECTORY, (
  _event,
  sessionId: unknown,
  relativePath: unknown,
  limit: unknown,
  workspaceRoot: unknown,
  projectId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof relativePath !== "string"
    || (limit !== undefined && (!Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 2_000))
    || (workspaceRoot !== undefined && typeof workspaceRoot !== "string")
    || (projectId !== undefined && typeof projectId !== "string")
  ) {
    throw new Error("Workspace 目录参数无效。");
  }
  return clientOrThrow().listWorkspaceDirectory(
    sessionId,
    relativePath,
    limit === undefined ? undefined : Number(limit),
    typeof workspaceRoot === "string" ? workspaceRoot : undefined,
    typeof projectId === "string" ? projectId : undefined,
  );
});

handleIpc(IPC.WORKSPACE_FIND_FILES, (
  _event,
  sessionId: unknown,
  query: unknown,
  limit: unknown,
  workspaceRoot: unknown,
  projectId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof query !== "string"
    || query.length > 256
    || (limit !== undefined && (!Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 500))
    || (workspaceRoot !== undefined && typeof workspaceRoot !== "string")
    || (projectId !== undefined && typeof projectId !== "string")
  ) {
    throw new Error("Workspace 文件搜索参数无效。");
  }
  return clientOrThrow().findWorkspaceFiles(
    sessionId,
    query,
    limit === undefined ? undefined : Number(limit),
    typeof workspaceRoot === "string" ? workspaceRoot : undefined,
    typeof projectId === "string" ? projectId : undefined,
  );
});

handleIpc(IPC.WORKSPACE_READ_FILE_PREVIEW, (
  _event,
  sessionId: unknown,
  relativePath: unknown,
  workspaceRoot: unknown,
  projectId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof relativePath !== "string"
    || (workspaceRoot !== undefined && typeof workspaceRoot !== "string")
    || (projectId !== undefined && typeof projectId !== "string")
  ) {
    throw new Error("Workspace 文件参数无效。");
  }
  return clientOrThrow().readWorkspaceFilePreview(
    sessionId,
    relativePath,
    typeof workspaceRoot === "string" ? workspaceRoot : undefined,
    typeof projectId === "string" ? projectId : undefined,
  );
});
function previewOwner(event: Electron.IpcMainInvokeEvent): Electron.WebContents {
  const owner = event.sender;
  if (BrowserWindow.fromWebContents(owner)?.webContents !== owner || event.senderFrame !== owner.mainFrame) throw new Error("预览请求来源无效。");
  return owner;
}
function previewString(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 8192 || value.includes("\0")) throw new Error("预览参数无效。");
  return value;
}
handleIpc(IPC.WORKSPACE_PREVIEW_URL, (
  event,
  id: unknown,
  path: unknown,
  version: unknown,
  workspaceRoot: unknown,
  projectId: unknown,
) => {
  if (version !== undefined && (typeof version !== "string" || !/^[a-f0-9]{64}$/.test(version))) throw new Error("文件版本无效。");
  if (workspaceRoot !== undefined && typeof workspaceRoot !== "string") throw new Error("Workspace 参数无效。");
  if (projectId !== undefined && typeof projectId !== "string") throw new Error("Project 参数无效。");
  return workspacePreviewUrl(() => artifactPreview.prepare(
    previewOwner(event),
    previewString(id),
    previewString(path),
    version as string | undefined,
    typeof workspaceRoot === "string" ? workspaceRoot : undefined,
    typeof projectId === "string" ? projectId : undefined,
  ));
});
handleIpc(IPC.WORKSPACE_RELEASE_PREVIEW, (event, url: unknown) => artifactPreview.release(previewOwner(event), previewString(url)));
handleIpc(IPC.BROWSER_OPEN, (
  event,
  id: unknown,
  browserId: unknown,
  url: unknown,
  workspaceRoot: unknown,
) => {
  if (workspaceRoot !== undefined && typeof workspaceRoot !== "string") throw new Error("Workspace 参数无效。");
  return artifactPreview.open(
    previewOwner(event),
    previewString(id),
    previewString(browserId),
    previewString(url),
    typeof workspaceRoot === "string" ? workspaceRoot : undefined,
  );
});
handleIpc(IPC.BROWSER_CLOSE, (event, id: unknown, browserId: unknown) => artifactPreview.close(previewOwner(event), previewString(id), previewString(browserId)));
handleIpc(IPC.BROWSER_STOP, (event, id: unknown, browserId: unknown) => artifactPreview.stop(previewOwner(event), previewString(id), previewString(browserId)));
handleIpc(IPC.BROWSER_STATE, (event, id: unknown, browserId: unknown) => artifactPreview.state(previewOwner(event), previewString(id), previewString(browserId)));
handleIpc(IPC.BROWSER_GO_BACK, (event, id: unknown, browserId: unknown) => artifactPreview.goBack(previewOwner(event), previewString(id), previewString(browserId)));
handleIpc(IPC.BROWSER_GO_FORWARD, (event, id: unknown, browserId: unknown) => artifactPreview.goForward(previewOwner(event), previewString(id), previewString(browserId)));
handleIpc(IPC.BROWSER_ANNOTATE, (event, id: unknown, browserId: unknown) => artifactPreview.annotate(previewOwner(event), previewString(id), previewString(browserId)));
handleIpc(IPC.BROWSER_BOUNDS, (event, id: unknown, browserId: unknown, bounds: unknown) => {
  if (bounds !== null && (typeof bounds !== "object" || !bounds || !["x", "y", "width", "height"].every((key) => typeof Reflect.get(bounds, key) === "number" && Number.isFinite(Reflect.get(bounds, key))))) throw new Error("页面位置无效。");
  artifactPreview.bounds(previewOwner(event), previewString(id), previewString(browserId), bounds as import("../shared/index.js").BrowserBounds | null);
});
handleIpc(IPC.WORKSPACE_OPEN_IN_EDITOR, async (
  _event,
  sessionId: unknown,
  relativePath: unknown,
  rootId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof relativePath !== "string" || (rootId !== undefined && typeof rootId !== "string")) {
    throw new Error("Workspace 文件参数无效。");
  }
  const snapshot = await clientOrThrow().readSession(sessionId);
  const selected = snapshot.session.roots?.find((root) => root.id === rootId);
  if (rootId !== undefined && !selected) throw new Error("文件夹不属于当前聊天。");
  const root = selected?.path ?? snapshot.session.roots?.[0]?.path ?? (snapshot.session.executionMode === "worktree"
    ? snapshot.session.worktree?.worktreeRoot : snapshot.session.workspaceRoot);
  if (!root) throw new Error("Workspace 当前不可用。");
  await clientOrThrow().readWorkspaceFilePreview(sessionId, relativePath, root);
  const canonicalTarget = await resolveWorkspaceFileForOpen(root, relativePath);
  const failure = await electronShell.openPath(canonicalTarget);
  if (failure) throw new Error("无法在编辑器中打开文件。");
});
handleIpc(IPC.WORKSPACE_SHOW_IN_FINDER, async (_event, targetPath: unknown) => {
  if (typeof targetPath !== "string" || !targetPath.trim()) {
    throw new Error("路径参数无效。");
  }
  try {
    const canonicalTarget = await realpath(targetPath);
    await stat(canonicalTarget);
    electronShell.showItemInFolder(canonicalTarget);
  } catch {
    throw new Error(process.platform === "win32"
      ? "无法在文件资源管理器中显示该路径。"
      : "无法在 Finder 中显示该路径。");
  }
});
handleIpc(IPC.WORKSPACE_OPEN_PATH, async (_event, targetPath: unknown) => {
  if (typeof targetPath !== "string" || !targetPath.trim()) {
    throw new Error("路径参数无效。");
  }
  try {
    const canonicalTarget = await realpath(targetPath);
    await stat(canonicalTarget);
    const failure = await electronShell.openPath(canonicalTarget);
    if (failure) throw new Error("无法打开该路径。");
  } catch {
    throw new Error("无法打开该路径。");
  }
});
handleIpc(IPC.TERMINAL_CREATE, (
  event,
  sessionId: unknown,
  workspaceRoot: unknown,
  projectId: unknown,
  preferredShell: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || (workspaceRoot !== undefined && typeof workspaceRoot !== "string")
    || (projectId !== undefined && typeof projectId !== "string")
    || (preferredShell !== undefined && typeof preferredShell !== "string")
  ) throw new Error("Terminal 参数无效。");
  return terminalManager.create(
    event.sender,
    sessionId,
    typeof workspaceRoot === "string" ? workspaceRoot : undefined,
    typeof projectId === "string" ? projectId : undefined,
    typeof preferredShell === "string" ? preferredShell : undefined,
  );
});
handleIpc(IPC.TERMINAL_WRITE, (event, terminalId: unknown, data: unknown) => {
  if (typeof terminalId !== "string" || typeof data !== "string") {
    throw new Error("终端输入参数无效。");
  }
  terminalManager.write(event.sender, terminalId, data);
});
handleIpc(IPC.TERMINAL_RESIZE, (
  event,
  terminalId: unknown,
  columns: unknown,
  rows: unknown,
) => {
  if (typeof terminalId !== "string" || typeof columns !== "number" || typeof rows !== "number") {
    throw new Error("终端尺寸参数无效。");
  }
  terminalManager.resize(event.sender, terminalId, columns, rows);
});
handleIpc(IPC.TERMINAL_CLOSE, (event, terminalId: unknown) => {
  if (typeof terminalId !== "string") throw new Error("终端参数无效。");
  terminalManager.close(event.sender, terminalId);
});
handleIpc(IPC.SESSION_GIT_DIFF, (
  _event,
  sessionId: unknown,
  scope: unknown,
  path: unknown,
  compareRef: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || (scope !== "head" && scope !== "baseline")
    || (path !== undefined && typeof path !== "string")
    || (compareRef !== undefined && (
      typeof compareRef !== "string" || !compareRef.trim()
    ))
  ) {
    throw new Error("Git Diff 参数无效。");
  }
  return clientOrThrow().readSessionGitDiff(
    sessionId,
    scope,
    path as string | undefined,
    compareRef as string | undefined,
  );
});
handleIpc(IPC.SESSION_GIT_SWITCH_BRANCH, (
  _event,
  sessionId: unknown,
  branch: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof branch !== "string"
    || !branch.trim()
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Switch Branch 参数无效。");
  }
  return clientOrThrow().switchSessionGitBranch(sessionId, branch, operationId);
});
handleIpc(IPC.SESSION_GIT_CREATE_BRANCH, (
  _event,
  sessionId: unknown,
  branch: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof branch !== "string"
    || !branch.trim()
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Create Branch 参数无效。");
  }
  return clientOrThrow().createSessionGitBranch(sessionId, branch, operationId);
});
handleIpc(IPC.SESSION_GIT_STAGE, (
  _event,
  sessionId: unknown,
  paths: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || !isNonEmptyStringArray(paths)
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Stage 参数无效。");
  }
  return clientOrThrow().stageSessionGit(sessionId, paths, operationId);
});
handleIpc(IPC.SESSION_GIT_UNSTAGE, (
  _event,
  sessionId: unknown,
  paths: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || !isNonEmptyStringArray(paths)
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Unstage 参数无效。");
  }
  return clientOrThrow().unstageSessionGit(sessionId, paths, operationId);
});
handleIpc(IPC.SESSION_GIT_DISCARD, (
  _event,
  sessionId: unknown,
  paths: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || !isNonEmptyStringArray(paths)
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Discard 参数无效。");
  }
  return clientOrThrow().discardSessionGit(sessionId, paths, operationId);
});
handleIpc(IPC.SESSION_GIT_STAGE_HUNK, (
  _event,
  sessionId: unknown,
  patch: unknown,
  reverse: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof patch !== "string"
    || !patch.trim()
    || typeof reverse !== "boolean"
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Stage Hunk 参数无效。");
  }
  return clientOrThrow().stageSessionGitHunk(sessionId, patch, reverse, operationId);
});
handleIpc(IPC.SESSION_GIT_SUGGEST_COMMIT_MESSAGE, (
  _event,
  sessionId: unknown,
  includeUnstaged: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || (includeUnstaged !== undefined && typeof includeUnstaged !== "boolean")
  ) {
    throw new Error("Git Suggest Commit Message 参数无效。");
  }
  return clientOrThrow().suggestSessionGitCommitMessage(
    sessionId,
    includeUnstaged === undefined ? true : includeUnstaged,
  );
});
handleIpc(IPC.CHECKPOINT_LIST, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string" || !sessionId.trim()) {
    throw new Error("Checkpoint 列表参数无效。");
  }
  return clientOrThrow().listCheckpoints(sessionId);
});
handleIpc(IPC.CHECKPOINT_REWIND, (
  _event,
  sessionId: unknown,
  checkpointId: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof checkpointId !== "string"
    || !checkpointId.trim()
    || (operationId !== undefined && typeof operationId !== "string")
  ) {
    throw new Error("Checkpoint 回退参数无效。");
  }
  return clientOrThrow().rewindCheckpoint(
    sessionId,
    checkpointId,
    operationId as string | undefined,
  );
});
handleIpc(IPC.SESSION_GIT_COMMIT, (
  _event,
  sessionId: unknown,
  message: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof message !== "string"
    || !message.trim()
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Commit 参数无效。");
  }
  return clientOrThrow().commitSessionGit(sessionId, message, operationId);
});
handleIpc(IPC.REVIEW_LIST_COMMENTS, (
  _event,
  sessionId: unknown,
  relativePath: unknown,
  scope: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || (relativePath !== undefined && typeof relativePath !== "string")
    || (scope !== undefined && scope !== "head" && scope !== "baseline")
    || ((relativePath === undefined) !== (scope === undefined))
  ) {
    throw new Error("Review Comment 参数无效。");
  }
  return clientOrThrow().listReviewComments(
    sessionId,
    relativePath as string | undefined,
    scope as "head" | "baseline" | undefined,
  );
});
handleIpc(IPC.REVIEW_CREATE_COMMENT, (
  _event,
  sessionId: unknown,
  input: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || !isReviewCommentCreateInput(input)
    || typeof operationId !== "string"
  ) {
    throw new Error("Review Comment 参数无效。");
  }
  return clientOrThrow().createReviewComment(sessionId, input, operationId);
});
handleIpc(IPC.REVIEW_DELETE_COMMENT, (
  _event,
  sessionId: unknown,
  commentId: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof commentId !== "string"
    || typeof operationId !== "string"
  ) {
    throw new Error("Review Comment 参数无效。");
  }
  return clientOrThrow().deleteReviewComment(sessionId, commentId, operationId);
});
handleIpc(IPC.SESSION_GIT_REMOTE_STATUS, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Git Remote 参数无效。");
  return clientOrThrow().readSessionGitRemoteStatus(sessionId);
});
handleIpc(IPC.SESSION_GIT_FETCH, (
  _event,
  sessionId: unknown,
  operationId: unknown,
  remote: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof operationId !== "string"
    || (remote !== undefined && typeof remote !== "string")
  ) {
    throw new Error("Git Fetch 参数无效。");
  }
  return clientOrThrow().fetchSessionGit(
    sessionId, operationId, remote as string | undefined,
  );
});
handleIpc(IPC.SESSION_GIT_PULL, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Pull 参数无效。");
  }
  return clientOrThrow().pullSessionGit(sessionId, operationId);
});
handleIpc(IPC.SESSION_GIT_PUSH, (
  _event,
  sessionId: unknown,
  operationId: unknown,
  remote: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof operationId !== "string"
    || (remote !== undefined && typeof remote !== "string")
  ) {
    throw new Error("Git Push 参数无效。");
  }
  return clientOrThrow().pushSessionGit(
    sessionId, operationId, remote as string | undefined,
  );
});
handleIpc(IPC.SESSION_GIT_MERGE, (
  _event,
  sessionId: unknown,
  target: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof target !== "string"
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Merge 参数无效。");
  }
  return clientOrThrow().mergeSessionGit(sessionId, target, operationId);
});
handleIpc(IPC.SESSION_GIT_MERGE_ABORT, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Merge Abort 参数无效。");
  }
  return clientOrThrow().abortSessionGitMerge(sessionId, operationId);
});
handleIpc(IPC.SESSION_GIT_REBASE, (
  _event,
  sessionId: unknown,
  target: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof target !== "string"
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Rebase 参数无效。");
  }
  return clientOrThrow().rebaseSessionGit(sessionId, target, operationId);
});
handleIpc(IPC.SESSION_GIT_REBASE_CONTINUE, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Rebase Continue 参数无效。");
  }
  return clientOrThrow().continueSessionGitRebase(sessionId, operationId);
});
handleIpc(IPC.SESSION_GIT_REBASE_ABORT, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Rebase Abort 参数无效。");
  }
  return clientOrThrow().abortSessionGitRebase(sessionId, operationId);
});

function inputOwner(event: Electron.IpcMainInvokeEvent): void {
  if (BrowserWindow.fromWebContents(event.sender)?.webContents !== event.sender || event.senderFrame !== event.sender.mainFrame) {
    throw new Error("输入请求来源无效。");
  }
}
function inputKey(value: unknown): string {
  if (typeof value !== "string" || !value || value.length > 128) throw new Error("输入标识无效。");
  return value;
}
handleIpc(IPC.INPUT_PICK, async (event, directory: unknown) => {
  inputOwner(event);
  if (typeof directory !== "boolean") throw new Error("文件选择参数无效。");
  const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender)!, {
    properties: [directory ? "openDirectory" : "openFile", "multiSelections"],
  });
  if (result.filePaths.length > 20) throw new Error("一次最多添加 20 个引用。");
  return result.canceled ? [] : result.filePaths;
});
handleIpc(IPC.INPUT_PREPARE, (event, request: unknown) => {
  inputOwner(event);
  if (!isInputPrepareRequest(request)) throw new Error("引用参数无效。");
  return clientOrThrow().prepareInput(request);
});
handleIpc(IPC.INPUT_READ, (event, id: unknown) => {
  inputOwner(event);
  return clientOrThrow().readInput(inputKey(id));
});
handleIpc(IPC.TOOL_CONTENT_PREVIEW_URL, (event, sessionId: unknown, toolCallId: unknown, index: unknown) => {
  if (typeof sessionId !== "string" || !sessionId || sessionId.length > 256
    || typeof toolCallId !== "string" || !toolCallId || toolCallId.length > 256
    || typeof index !== "number" || !Number.isSafeInteger(index) || index < 0 || index > 31) {
    throw new Error("图像参数无效。");
  }
  return artifactPreview.prepareToolContent(previewOwner(event), sessionId, toolCallId, index);
});
handleIpc(IPC.INPUT_PREVIEW_URL, (event, id: unknown) => {
  return artifactPreview.prepareInput(previewOwner(event), inputKey(id));
});
handleIpc(IPC.INPUT_DRAFT_READ, (event, key: unknown) => {
  inputOwner(event);
  return clientOrThrow().readInputDraft(inputKey(key));
});
handleIpc(IPC.INPUT_DRAFT_WRITE, (event, key: unknown, draft: unknown) => {
  inputOwner(event);
  if (!isInputDraft(draft)) throw new Error("草稿参数无效。");
  return clientOrThrow().writeInputDraft(inputKey(key), draft);
});
function runtimeDataDirectory(): string {
  return process.env.EIDOS_DATA_DIR ?? path.join(app.getPath("home"), ".eidos");
}
// Without a source, the clipboard bitmap is pasted. With one, the source must be
// a file the pasteboard lists; Main hands the Runtime a private copy so files
// the selected-file checks reject (other owner, hard links, removed temp files,
// WebP named .jpg) can still be pasted as images.
handleIpc(IPC.INPUT_PASTE_IMAGE, async (event, source: unknown) => {
  inputOwner(event);
  let bytes: Buffer;
  let extension = ".png";
  if (source === undefined) {
    const picture = clipboard.readImage();
    if (picture.isEmpty()) return null;
    bytes = picture.toPNG();
  } else {
    ({ bytes, extension } = await readPastedImageFile(source, {
      clipboardPaths: process.platform === "win32"
        ? await windowsClipboardFilePaths((format) => clipboard.readBuffer(format))
        : clipboardFilePaths((format) => clipboard.read(format)),
      dataDirectory: runtimeDataDirectory(),
    }));
  }
  if (bytes.length > MAX_PASTED_IMAGE_BYTES) throw new Error("图片不能超过 10 MiB。");
  const directory = await mkdtemp(path.join(os.tmpdir(), "eidos-input-"));
  try {
    const filename = path.join(directory, `粘贴的图片${extension}`);
    await writeFile(filename, bytes, { mode: 0o600, flag: "wx" });
    return await clientOrThrow().prepareInput({ kind: "image", source: filename, origin: "clipboard" });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

handleIpc(IPC.AGENT_CATALOG, (event) => {
  inputOwner(event);
  return clientOrThrow().listAgentTypes();
});
handleIpc(IPC.AGENT_DEFINITION_READ, (event, source: unknown) => {
  inputOwner(event);
  return clientOrThrow().readAgentDefinition(boundedAgentSource(source));
});
handleIpc(IPC.AGENT_DEFINITION_SAVE, (event, request: unknown) => {
  inputOwner(event);
  if (!isAgentDefinitionSaveRequest(request)) throw new Error("智能体配置无效，请检查填写内容。");
  return clientOrThrow().saveAgentDefinition(request);
});
handleIpc(IPC.AGENT_DEFINITION_REMOVE, (event, request: unknown) => {
  inputOwner(event);
  if (!isAgentDefinitionRemoveRequest(request)) throw new Error("智能体配置版本无效，请刷新后重试。");
  return clientOrThrow().removeAgentDefinition(request);
});
function boundedAgentSource(source: unknown): string {
  if (typeof source !== "string" || !source || source.length > 4096) throw new Error("智能体配置路径无效。");
  return source;
}
handleIpc(IPC.AGENT_READ, (event, sessionId: unknown) => {
  inputOwner(event);
  return clientOrThrow().readAgents(inputKey(sessionId));
});
handleIpc(IPC.AGENT_STOP, (event, parentRunId: unknown, agentId: unknown) => {
  inputOwner(event);
  return clientOrThrow().stopAgent(inputKey(parentRunId), inputKey(agentId));
});
handleIpc(IPC.AGENT_MESSAGE, (event, request: unknown) => {
  inputOwner(event);
  if (!isAgentMessageFromUserRequest(request)) throw new Error("Invalid agent message request.");
  return clientOrThrow().messageAgent(request);
});
handleIpc(IPC.AGENT_CHANGES, (event, request: unknown) => {
  inputOwner(event);
  if (!isAgentChangesRequest(request)) throw new Error("Invalid agent changes request.");
  return clientOrThrow().readAgentChanges(request);
});
handleIpc(IPC.AGENT_DISCARD_WORKSPACE, (event, parentRunId: unknown, agentId: unknown) => {
  inputOwner(event);
  return clientOrThrow().discardAgentWorkspace(inputKey(parentRunId), inputKey(agentId));
});
handleIpc(IPC.DATA_BACKUP, async (event) => {
  inputOwner(event);
  const owner = BrowserWindow.fromWebContents(event.sender);
  if (!owner) throw new Error("无法打开备份保存窗口。");
  const result = await dialog.showSaveDialog(owner, {title: "备份本地数据（明文）", defaultPath: "eidos-data-backup.zip", filters: [{name: "ZIP", extensions: ["zip"]}]});
  if (!result.canceled && result.filePath) await clientOrThrow().memory("data/backup", {destination: result.filePath});
});
handleIpc(IPC.DATA_RESTORE, async (event) => {
  inputOwner(event);
  if (activeRunProjectionMap.size > 0) {
    throw new Error("当前仍有活动任务正在执行，请先停止任务后再恢复备份。");
  }
  const owner = BrowserWindow.fromWebContents(event.sender);
  if (!owner) throw new Error("无法打开备份文件选择窗口。");
  const picked = await dialog.showOpenDialog(owner, {
    title: "选择备份压缩包恢复数据",
    properties: ["openFile"],
    filters: [{ name: "ZIP", extensions: ["zip"] }],
  });
  const archivePath = picked.filePaths[0];
  if (picked.canceled || !archivePath) {
    return runtimeStatus;
  }
  const runtimePaths = resolveRuntimePaths({
    isPackaged: app.isPackaged,
    appPath: app.getAppPath(),
    resourcesPath: process.resourcesPath,
    environment: process.env,
  });
  return restoreDataBackup({
    archivePath,
    dataDirectory: runtimeDataDirectory(),
    activeRunCount: activeRunProjectionMap.size,
    runOfflineRestore: async (archive, destination) => {
      const environment = buildRuntimeEnvironment({
        runtimeRoot: runtimePaths.runtimeRoot,
        baseEnvironment: process.env,
        overrides: {},
        policy: app.isPackaged ? "packaged" : "development",
      });
      await execFileAsync(
        runtimePaths.pythonExecutable,
        ["-m", "eidos_runtime.db.backup", archive, destination],
        { env: environment, maxBuffer: 2 * 1024 * 1024 },
      );
    },
    shutdownRuntime: async () => {
      const previous = runtimeClient;
      runtimeClient = undefined;
      runtimeStartGeneration += 1;
      clearRuntimeProcessState();
      publishStatus({ state: "starting" });
      if (previous) {
        try {
          await previous.shutdown();
        } catch {
          previous.terminate();
        }
        await waitForRuntimeExit(previous);
      }
    },
    startRuntime: async () => {
      await startRuntime();
      return runtimeStatus;
    },
    isStorageReady: (status) => status.state === "ready" && status.storageHealth.state === "ready",
  });
});
handleIpc(IPC.MEMORY_REQUEST, (event, method: unknown, request: unknown) => {
  inputOwner(event);
  if (!isMemoryMethod(method) || method === "data/backup" || !isMemoryRequest(method, request)) throw new Error("记忆请求无效。");
  return clientOrThrow().memory(method, request);
});
handleIpc(IPC.MEMORY_EXPORT, async (event, request: unknown) => {
  inputOwner(event);
  if (!isMemoryRequest("memory/export", request)) throw new Error("记忆请求无效。");
  const owner = BrowserWindow.fromWebContents(event.sender);
  if (!owner) throw new Error("无法打开保存窗口。");
  const saved = await dialog.showSaveDialog(owner, { title: "导出记忆", defaultPath: "eidos-memories.md", filters: [{ name: "Markdown", extensions: ["md"] }] });
  if (!saved.canceled && saved.filePath) await exportMemoryNotes(saved.filePath, { ...request, cursor: 0 }, (page) => clientOrThrow().memory("memory/export", page));
});
handleIpc(IPC.PLANNING_READ, (event, sessionId: unknown) => {
  inputOwner(event);
  return clientOrThrow().readPlanning(inputKey(sessionId));
});
handleIpc(IPC.PLANNING_ANSWER, (event, request: unknown) => {
  inputOwner(event);
  if (!isAnswerInputRequest(request)) throw new Error("澄清回答无效。");
  return clientOrThrow().answerUserInput(request);
});
handleIpc(IPC.PLAN_READ, (event, planId: unknown, reloadFile: unknown) => {
  inputOwner(event);
  if (typeof reloadFile !== "boolean") throw new Error("计划参数无效。");
  return clientOrThrow().readPlan(inputKey(planId), reloadFile);
});
handleIpc(IPC.PLAN_EDIT, (event, request: unknown) => {
  inputOwner(event);
  if (!isPlanEditRequest(request)) throw new Error("计划修改无效。");
  return clientOrThrow().editPlan(request);
});
handleIpc(IPC.PLAN_OPEN, async (event, planId: unknown) => {
  inputOwner(event);
  const plan = await clientOrThrow().readPlan(inputKey(planId));
  const error = await electronShell.openPath(plan.path);
  if (error) throw new Error(error);
});

handleIpc(IPC.RUN_START, async (
  _event,
  sessionId: unknown,
  userInput: unknown,
  modelId: unknown,
  reasoningSelection: unknown,
  approvalMode: unknown,
  references: unknown,
  planning: unknown,
) => {
  inputOwner(_event);
  const validReasoningSelection =
    reasoningSelection === undefined
    || (
      typeof reasoningSelection === "string"
      && ["none", "thinking", "low", "medium", "high", "xhigh", "max"].includes(
        reasoningSelection,
      )
    );
  if (
    typeof sessionId !== "string"
    || typeof userInput !== "string"
    || typeof modelId !== "string"
    || modelId.length === 0
    || modelId.length > 256
    || (references !== undefined && (!Array.isArray(references) || references.length > 20 || !references.every((id) => typeof id === "string" && /^[a-f0-9]{64}$/.test(id))))
    || (planning !== undefined && !isRunPlanningOptions(planning))
    || !validReasoningSelection
    || (approvalMode !== undefined && !["manual", "auto_review", "full_access"].includes(String(approvalMode)))
  ) {
    throw new Error("Run 参数无效。");
  }
  return clientOrThrow().startRun(
    sessionId,
    userInput,
    modelId,
    undefined,
    reasoningSelection as ModelReasoningSelection | undefined,
    approvalMode as ApprovalMode | undefined,
    approvalMode === "full_access" ? "full-access-v1" : undefined,
    references as string[] | undefined,
    planning as RunPlanningOptions | undefined,
  );
});
handleIpc(IPC.RUN_RESUME, (_event, runId: unknown) => {
  if (typeof runId !== "string" || !runId || runId.length > 256) throw new Error("任务参数无效");
  return clientOrThrow().resumeRun(runId);
});
handleIpc(IPC.RUN_CANCEL, (_event, runId: unknown) => {
  if (typeof runId !== "string") throw new Error("Run 参数无效。");
  log("info", "run", "Cancel requested", { runId });
  return clientOrThrow().cancelRun(runId);
});
handleIpc(IPC.CONTEXT_USAGE, (_event, runId: unknown) => {
  if (typeof runId !== "string") throw new Error("Context Usage 参数无效。");
  return clientOrThrow().readContextUsage(runId);
});
handleIpc(IPC.CONTEXT_DETAILS, (_event, runId: unknown) => {
  if (typeof runId !== "string") throw new Error("上下文参数无效。");
  return clientOrThrow().readContextDetails(runId);
});
handleIpc(IPC.CONTEXT_COMPACT, (_event, runId: unknown) => {
  if (typeof runId !== "string") throw new Error("上下文参数无效。");
  return clientOrThrow().requestContextCompaction(runId);
});
handleIpc(IPC.RUN_REVISE, (_event, sourceRunId: unknown, userInput: unknown, references: unknown, approvalMode: unknown) => {
  inputOwner(_event);
  if (
    (references !== undefined && (!Array.isArray(references) || references.length > 20 || !references.every((id) => typeof id === "string" && /^[a-f0-9]{64}$/.test(id))))
    || typeof sourceRunId !== "string"
    || (approvalMode !== undefined && (typeof approvalMode !== "string" || !["manual", "auto_review", "full_access"].includes(approvalMode)))
    || (userInput !== undefined && typeof userInput !== "string")
    || (typeof userInput === "string" && (
      (!userInput.trim() && !(Array.isArray(references) && references.length)) || Buffer.byteLength(userInput, "utf8") > 64 * 1024
    ))
  ) {
    throw new Error("重新回答参数无效。");
  }
  log("info", "run", "Run revision requested", {
    sourceRunId,
    kind: userInput === undefined ? "regenerate" : "edit",
  });
  return clientOrThrow().reviseRun(
    sourceRunId,
    userInput,
    undefined,
    references as string[] | undefined,
    approvalMode as ApprovalMode | undefined,
    approvalMode === "full_access" ? "full-access-v1" : undefined,
  );
});

handleIpc(IPC.RESPONSE_ACTION_STATE, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("回复操作参数无效。");
  return clientOrThrow().readResponseActionState(sessionId);
});
handleIpc(IPC.ITEM_SET_FEEDBACK, (_event, itemId: unknown, feedback: unknown) => {
  if (
    typeof itemId !== "string"
    || (feedback !== null && !["up", "down"].includes(String(feedback)))
  ) {
    throw new Error("回复反馈参数无效。");
  }
  log("info", "feedback", "Response feedback requested", { itemId, feedback });
  return clientOrThrow().setItemFeedback(
    itemId,
    feedback as ResponseFeedbackValue | null,
  );
});

handleIpc(IPC.MODEL_QODER, (_event, input: unknown) => {
  if (!isQoderConnectionRequest(input)) throw new Error("Qoder 参数无效。");
  return clientOrThrow().qoderConnection(input);
});
handleIpc(IPC.MODEL_DISCOVER_CUSTOM, (_event, input: unknown) => {
  if (!input || typeof input !== "object") throw new Error("自定义提供商参数无效。");
  return clientOrThrow().discoverCustomModels(input as CustomProviderRequest);
});
handleIpc(IPC.MODEL_PRESETS, () => clientOrThrow().listModelPresets());
handleIpc(IPC.MODEL_LIST, () => clientOrThrow().listModels());
handleIpc(IPC.MODEL_CREATE, (_event, input: unknown) => {
  if (!input || typeof input !== "object") throw new Error("模型参数无效。");
  return clientOrThrow().createModel(input as ModelCreateInput);
});
handleIpc(IPC.MODEL_UPDATE, (_event, input: unknown) => {
  if (!input || typeof input !== "object") throw new Error("模型参数无效。");
  return clientOrThrow().updateModel(input as ModelUpdateInput);
});
handleIpc(IPC.MODEL_DELETE, (_event, id: unknown) => {
  if (typeof id !== "string") throw new Error("模型参数无效。");
  return clientOrThrow().deleteModel(id);
});
handleIpc(IPC.HOOK_CONFIGURATION, (_event, request: unknown) => {
  if (!isHookConfigurationRequest(request)) throw new Error("Hook 配置参数无效。");
  return clientOrThrow().hookConfiguration(request);
});
handleIpc(IPC.HOOK_HISTORY, (_event, request: unknown) => {
  if (!isHookHistoryRequest(request)) throw new Error("Hook 历史参数无效。");
  return clientOrThrow().readHookHistory(request);
});
handleIpc(IPC.PLUGIN_LIST, () => clientOrThrow().listPlugins());
handleIpc(IPC.PLUGIN_BUILTIN, (_event, request: unknown) => {
  if (!isBuiltinPluginRequest(request)) throw new Error("INVALID_PARAMS");
  return clientOrThrow().builtinPlugins(request);
});
handleIpc(IPC.MCP_CONNECTION, (_event, request: unknown) => {
  if (!isMcpConnectionRequest(request)) throw new Error("INVALID_PARAMS");
  return clientOrThrow().mcpConnection(request);
});
handleIpc(IPC.MCP_LOGIN_OPEN, async (event, request: unknown) => {
  previewOwner(event);
  if (!isMcpConnectionRequest(request) || (request.action && request.action !== "read")) throw new Error("INVALID_PARAMS");
  const state = await clientOrThrow().mcpConnection(request);
  if (request.method === "token" && state.authType === "github-device") {
    await electronShell.openExternal("https://github.com/settings/personal-access-tokens");
    return;
  }
  if (state.status !== "connecting" || !state.authorizationUrl) throw new Error("登录已结束，请重新连接。");
  const url = new URL(state.authorizationUrl);
  if (url.protocol !== "https:" || url.username || url.password) throw new Error("登录地址无效。");
  await electronShell.openExternal(url.toString());
});
handleIpc(IPC.PLUGIN_IMPORT, async () => {
  const result = await dialog.showOpenDialog({
    title: "导入本地 Eidos Plugin",
    properties: ["openDirectory"],
  });
  const sourcePath = result.canceled ? undefined : result.filePaths[0];
  return sourcePath ? clientOrThrow().importPlugin(sourcePath) : null;
});
handleIpc(IPC.PLUGIN_SET_ENABLED, (_event, pluginId: unknown, enabled: unknown) => {
  if (typeof pluginId !== "string" || typeof enabled !== "boolean") {
    throw new Error("Plugin 参数无效。");
  }
  return clientOrThrow().setPluginEnabled(pluginId, enabled);
});
handleIpc(IPC.PLUGIN_REMOVE, (_event, pluginId: unknown) => {
  if (typeof pluginId !== "string") throw new Error("Plugin 参数无效。");
  return clientOrThrow().removePlugin(pluginId);
});
handleIpc(IPC.PLUGIN_SET_HOOK_TRUST, (_event, pluginId: unknown, contentHash: unknown, trusted: unknown) => {
  if (typeof pluginId !== "string" || typeof contentHash !== "string" || !/^[0-9a-f]{64}$/.test(contentHash) || typeof trusted !== "boolean") {
    throw new Error("Hook 信任参数无效。");
  }
  return clientOrThrow().setPluginHookTrust(pluginId, contentHash, trusted);
});
handleIpc(IPC.PLUGIN_ACTIVATE, (_event, pluginId: unknown, contentHash: unknown) => {
  if (typeof pluginId !== "string" || typeof contentHash !== "string" || !/^[0-9a-f]{64}$/.test(contentHash)) {
    throw new Error("Plugin 参数无效。");
  }
  return clientOrThrow().activatePlugin(pluginId, contentHash);
});

handleIpc(IPC.SKILL_LIST, () => clientOrThrow().listSkills());
handleIpc(IPC.RESOURCE_STATUS, () => clientOrThrow().getResourceStatus());
handleIpc(IPC.RESOURCE_RETRY, (_event, request: unknown) => {
  if (!isResourceRetryRequest(request)) throw new Error("资源参数无效。");
  return clientOrThrow().retryResource(request);
});
handleIpc(IPC.RESOURCE_CLEANUP, () => clientOrThrow().cleanupResources());
function skillId(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || value.length > 256) {
    throw new Error("技能参数无效。");
  }
  return value;
}
handleIpc(IPC.SKILL_DETAIL, (_event, id: unknown) =>
  clientOrThrow().readSkillDetail(skillId(id)));
handleIpc(IPC.SKILL_SET_ENABLED, (_event, id: unknown, enabled: unknown) => {
  if (typeof enabled !== "boolean") throw new Error("技能状态无效。");
  return clientOrThrow().setSkillEnabled(skillId(id), enabled);
});
handleIpc(IPC.SKILL_REMOVE, (_event, id: unknown) =>
  clientOrThrow().removeSkill(skillId(id)));
handleIpc(IPC.MCP_LIST, () => clientOrThrow().listMcpServers());
handleIpc(IPC.MCP_TOOLS, (_event, request: unknown) => {
  if (!isMcpToolsRequest(request)) throw new Error("INVALID_PARAMS");
  return clientOrThrow().readMcpTools(request);
});
handleIpc(IPC.BROWSER_AGENT_STATUS, (_event, request: unknown) => {
  if (!isBrowserStatusRequest(request)) throw new Error("INVALID_PARAMS");
  return clientOrThrow().readBrowserStatus(request);
});
handleIpc(IPC.BROWSER_AGENT_REVOKE, (_event, request: unknown) => {
  if (!isBrowserGrantRevokeRequest(request)) throw new Error("INVALID_PARAMS");
  return clientOrThrow().revokeBrowserGrant(request);
});
handleIpc(IPC.BROWSER_AGENT_CLOSE, () => clientOrThrow().closeSharedBrowser());
handleIpc(IPC.BROWSER_AGENT_OPEN, () => clientOrThrow().openSharedBrowser());
handleIpc(IPC.BROWSER_AGENT_CLEAR, () => clientOrThrow().clearSharedBrowserProfile());
handleIpc(IPC.BROWSER_AGENT_TARGET, (_event, request: unknown) => {
  if (!isBrowserTargetRequest(request)) throw new Error("INVALID_PARAMS");
  return clientOrThrow().setBrowserTarget(request);
});
handleIpc(IPC.BROWSER_AGENT_EXTENSION_TOKEN, (_event, request: unknown) => {
  if (!isBrowserExtensionTokenRequest(request)) throw new Error("INVALID_PARAMS");
  return clientOrThrow().setBrowserExtensionToken(request);
});
handleIpc(IPC.BROWSER_AGENT_EXTENSION_CLEAR, () => clientOrThrow().clearBrowserExtensionToken());
handleIpc(IPC.BROWSER_AGENT_EXTENSION_STORE, (event) => {
  previewOwner(event);
  return electronShell.openExternal(PLAYWRIGHT_EXTENSION_STORE_URL);
});
handleIpc(IPC.EXTENSION_READ, () => clientOrThrow().readExtensions());
handleIpc(IPC.EXTENSION_READ_EVENTS, (_event, afterEventId: unknown) => {
  if (typeof afterEventId !== "number") throw new Error("Extension Event 参数无效。");
  return clientOrThrow().readExtensionEvents(afterEventId);
});
handleIpc(IPC.MCP_SET_ENABLED, (_event, pluginId: unknown, serverId: unknown, enabled: unknown) => {
  if (typeof pluginId !== "string" || typeof serverId !== "string" || typeof enabled !== "boolean") {
    throw new Error("MCP Server 参数无效。");
  }
  return clientOrThrow().setMcpEnabled(pluginId, serverId, enabled);
});
handleIpc(IPC.MCP_CREATE, (_event, input: unknown) => {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("MCP Server 参数无效。");
  }
  return clientOrThrow().createMcpServer(input as McpCreateInput);
});
handleIpc(IPC.MCP_UPDATE, (_event, input: unknown) => {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("MCP Server 参数无效。");
  }
  return clientOrThrow().updateMcpServer(input as McpUpdateInput);
});
handleIpc(IPC.MCP_REMOVE, (_event, serverId: unknown) => {
  if (typeof serverId !== "string") throw new Error("MCP Server 参数无效。");
  return clientOrThrow().removeMcpServer(serverId);
});

handleIpc(IPC.APPROVAL_LIST, () =>
  Array.from(pendingApprovals.values(), ({ request }) => request),
);
handleIpc(IPC.APPROVAL_RESPOND, (_event, id: unknown, decision: unknown, feedback: unknown) => {
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
    if (quitFlowController.getState().quitCommitted) return;
    const window = BrowserWindow.getAllWindows()[0];
    if (!window || window.isDestroyed()) return;
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  });

  app.whenReady().then(() => {
    try {
      app.setPath("crashDumps", path.join(runtimeDataDirectory(), "crash-dumps"));
      crashReporter.start({ uploadToServer: false, compress: false });
    } catch (error) {
      log("warn", "crash", "Failed to start local crashReporter", {
        message: error instanceof Error ? error.message : String(error),
      });
    }
    powerMonitor.on("resume", () => {
      if (quitFlowController.getState().quitCommitted) return;
      void runtimeClient?.refreshAutomationSchedule().catch(() => undefined);
    });
    artifactPreview.install();
    buildMenu();
    ensureSystemTray();
    createWindow();
    void startRuntime();

    app.on("activate", () => {
      if (!quitFlowController.getState().quitCommitted && BrowserWindow.getAllWindows().length === 0) {
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

app.on("will-quit", () => {
  destroySystemTray();
  artifactPreview.closeAll();
  terminalManager.closeAll();
  terminateRuntimeOnce();
});

app.on("window-all-closed", () => {
  const keepAliveForTray = Boolean(systemTray) && process.env.EIDOS_DISABLE_TRAY !== "1";
  if (process.platform !== "darwin" && !keepAliveForTray && !quitFlowController.getState().quitCommitted) {
    app.quit();
  }
});
import { isResourceRetryRequest } from "../shared/managed-resources.js";
