import { app, BrowserWindow, dialog, ipcMain, Menu, shell as electronShell } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { RuntimeClient } from "./runtime-client.js";
import { resolveRuntimePaths } from "./runtime-paths.js";
import "./response-runtime-client.js";
import { redactLogLine, sanitizeLogValue } from "./log-redaction.js";
import { dispatchAppCommand as dispatchCommand, ensureAppWindow as ensureWindow } from "./app-command-dispatch.js";
import { QuitFlowController, type ActiveRunProjection, type QuitFlowDependencies } from "./quit-flow.js";
import { shutdownRuntime } from "./runtime-shutdown.js";
import { resolveWorkspaceFileForOpen } from "./workspace-open.js";
import type {
  ApprovalDecision,
  ApprovalRequest,
  AppShortcut,
  RuntimeNotification,
  RuntimeStatus,
  ModelCreateInput,
  ModelUpdateInput,
  ResponseFeedbackValue,
  ReviewCommentCreateInput,
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

function validateSessionCreateOptions(value: unknown): {
  executionMode?: "local" | "worktree";
  baseRef?: string;
  includeLocalChanges?: boolean;
} {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Session 创建参数无效。");
  }
  const record = value as Record<string, unknown>;
  if (Object.keys(record).some((key) => !["executionMode", "baseRef", "includeLocalChanges"].includes(key))) {
    throw new Error("Session 创建参数无效。");
  }
  if (
    record.executionMode !== undefined
    && record.executionMode !== "local"
    && record.executionMode !== "worktree"
  ) {
    throw new Error("Session 创建模式无效。");
  }
  if (record.baseRef !== undefined && typeof record.baseRef !== "string") {
    throw new Error("Session 起始 Ref 无效。");
  }
  if (record.includeLocalChanges !== undefined && typeof record.includeLocalChanges !== "boolean") {
    throw new Error("Session 本地修改参数无效。");
  }
  return {
    ...(record.executionMode !== undefined
      ? { executionMode: record.executionMode }
      : {}),
    ...(record.baseRef !== undefined ? { baseRef: record.baseRef } : {}),
    ...(record.includeLocalChanges !== undefined
      ? { includeLocalChanges: record.includeLocalChanges }
      : {}),
  };
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
  try {
    const runtimePaths = resolveRuntimePaths({
      isPackaged: app.isPackaged,
      appPath: app.getAppPath(),
      resourcesPath: process.resourcesPath,
      environment: process.env,
    });
    const client = new RuntimeClient({
      pythonExecutable: runtimePaths.pythonExecutable,
      runtimeRoot: runtimePaths.runtimeRoot,
      dataDirectory: process.env.EIDOS_DATA_DIR ?? path.join(app.getPath("home"), ".eidos"),
      environmentPolicy: app.isPackaged ? "packaged" : "development",
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
ipcMain.handle(IPC.SESSION_CREATE, (_event, workspaceRoot: unknown, options: unknown) => {
  if (typeof workspaceRoot !== "string") throw new Error("Workspace 参数无效。");
  return clientOrThrow().createSession(workspaceRoot, validateSessionCreateOptions(options));
});
ipcMain.handle(IPC.SESSION_CREATE_BRANCH, (_event, sessionId: unknown, branch: unknown) => {
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
ipcMain.handle(IPC.SESSION_HANDOFF, (_event, sessionId: unknown, target: unknown) => {
  if (
    typeof sessionId !== "string"
    || (target !== "local" && target !== "worktree")
  ) {
    throw new Error("Handoff 参数无效。");
  }
  return clientOrThrow().handoffSession(sessionId, target);
});
ipcMain.handle(IPC.SESSION_RESTORE_WORKTREE, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  return clientOrThrow().restoreSessionWorktree(sessionId);
});
ipcMain.handle(IPC.WORKTREE_SETTINGS_READ, () => clientOrThrow().readWorktreeSettings());
ipcMain.handle(IPC.WORKTREE_SETTINGS_UPDATE, (_event, input: unknown) => {
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
ipcMain.handle(IPC.PROJECT_GIT_CONTEXT, (_event, workspaceRoot: unknown) => {
  if (typeof workspaceRoot !== "string") throw new Error("Workspace 参数无效。");
  return clientOrThrow().readProjectGitContext(workspaceRoot);
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
ipcMain.handle(IPC.SESSION_GIT_STATUS, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Session 参数无效。");
  return clientOrThrow().readSessionGitStatus(sessionId);
});
ipcMain.handle(IPC.WORKSPACE_LIST_DIRECTORY, (
  _event,
  sessionId: unknown,
  relativePath: unknown,
  limit: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof relativePath !== "string"
    || (limit !== undefined && (!Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 2_000))
  ) {
    throw new Error("Workspace 目录参数无效。");
  }
  return clientOrThrow().listWorkspaceDirectory(
    sessionId,
    relativePath,
    limit === undefined ? undefined : Number(limit),
  );
});
ipcMain.handle(IPC.WORKSPACE_READ_FILE_PREVIEW, (
  _event,
  sessionId: unknown,
  relativePath: unknown,
) => {
  if (typeof sessionId !== "string" || typeof relativePath !== "string") {
    throw new Error("Workspace 文件参数无效。");
  }
  return clientOrThrow().readWorkspaceFilePreview(sessionId, relativePath);
});
ipcMain.handle(IPC.WORKSPACE_OPEN_IN_EDITOR, async (
  _event,
  sessionId: unknown,
  relativePath: unknown,
) => {
  if (typeof sessionId !== "string" || typeof relativePath !== "string") {
    throw new Error("Workspace 文件参数无效。");
  }
  const snapshot = await clientOrThrow().readSession(sessionId);
  const root = snapshot.session.executionMode === "worktree"
    ? snapshot.session.worktree?.worktreeRoot
    : snapshot.session.workspaceRoot;
  if (!root) throw new Error("Workspace 当前不可用。");
  const canonicalTarget = await resolveWorkspaceFileForOpen(root, relativePath);
  const failure = await electronShell.openPath(canonicalTarget);
  if (failure) throw new Error("无法在编辑器中打开文件。");
});
ipcMain.handle(IPC.SESSION_GIT_DIFF, (
  _event,
  sessionId: unknown,
  scope: unknown,
  path: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || (scope !== "head" && scope !== "baseline")
    || (path !== undefined && typeof path !== "string")
  ) {
    throw new Error("Git Diff 参数无效。");
  }
  return clientOrThrow().readSessionGitDiff(sessionId, scope, path as string | undefined);
});
ipcMain.handle(IPC.SESSION_GIT_STAGE, (
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
ipcMain.handle(IPC.SESSION_GIT_UNSTAGE, (
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
ipcMain.handle(IPC.SESSION_GIT_COMMIT, (
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
ipcMain.handle(IPC.SESSION_GIT_DISCARD, (
  _event,
  sessionId: unknown,
  relativePath: unknown,
  operationId: unknown,
) => {
  if (
    typeof sessionId !== "string"
    || typeof relativePath !== "string"
    || typeof operationId !== "string"
  ) {
    throw new Error("Git Discard 参数无效。");
  }
  return clientOrThrow().discardSessionGit(sessionId, relativePath, operationId);
});
ipcMain.handle(IPC.REVIEW_LIST_COMMENTS, (
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
ipcMain.handle(IPC.REVIEW_CREATE_COMMENT, (
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
ipcMain.handle(IPC.REVIEW_DELETE_COMMENT, (
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
ipcMain.handle(IPC.SESSION_GIT_REMOTE_STATUS, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("Git Remote 参数无效。");
  return clientOrThrow().readSessionGitRemoteStatus(sessionId);
});
ipcMain.handle(IPC.SESSION_GIT_FETCH, (
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
ipcMain.handle(IPC.SESSION_GIT_PULL, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Pull 参数无效。");
  }
  return clientOrThrow().pullSessionGit(sessionId, operationId);
});
ipcMain.handle(IPC.SESSION_GIT_PUSH, (
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
ipcMain.handle(IPC.SESSION_GIT_MERGE, (
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
ipcMain.handle(IPC.SESSION_GIT_MERGE_ABORT, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Merge Abort 参数无效。");
  }
  return clientOrThrow().abortSessionGitMerge(sessionId, operationId);
});
ipcMain.handle(IPC.SESSION_GIT_REBASE, (
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
ipcMain.handle(IPC.SESSION_GIT_REBASE_CONTINUE, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Rebase Continue 参数无效。");
  }
  return clientOrThrow().continueSessionGitRebase(sessionId, operationId);
});
ipcMain.handle(IPC.SESSION_GIT_REBASE_ABORT, (
  _event,
  sessionId: unknown,
  operationId: unknown,
) => {
  if (typeof sessionId !== "string" || typeof operationId !== "string") {
    throw new Error("Git Rebase Abort 参数无效。");
  }
  return clientOrThrow().abortSessionGitRebase(sessionId, operationId);
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
ipcMain.handle(IPC.CONTEXT_USAGE, (_event, runId: unknown) => {
  if (typeof runId !== "string") throw new Error("Context Usage 参数无效。");
  return clientOrThrow().readContextUsage(runId);
});
ipcMain.handle(IPC.RUN_REVISE, (_event, sourceRunId: unknown, userInput: unknown) => {
  if (
    typeof sourceRunId !== "string"
    || (userInput !== undefined && typeof userInput !== "string")
    || (typeof userInput === "string" && (
      !userInput.trim() || Buffer.byteLength(userInput, "utf8") > 64 * 1024
    ))
  ) {
    throw new Error("重新回答参数无效。");
  }
  log("info", "run", "Run revision requested", {
    sourceRunId,
    kind: userInput === undefined ? "regenerate" : "edit",
  });
  return clientOrThrow().reviseRun(sourceRunId, userInput);
});

ipcMain.handle(IPC.RESPONSE_ACTION_STATE, (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") throw new Error("回复操作参数无效。");
  return clientOrThrow().readResponseActionState(sessionId);
});
ipcMain.handle(IPC.ITEM_SET_FEEDBACK, (_event, itemId: unknown, feedback: unknown) => {
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
