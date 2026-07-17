import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { RuntimeClient } from "./runtime-client.js";
import type {
  ApprovalDecision,
  ApprovalRequest,
  RuntimeNotification,
} from "./runtime-client.js";


type RuntimeStatus =
  | { state: "starting" }
  | {
      state: "ready";
      protocolVersion: number;
      runtimeVersion: string;
      runShell: boolean;
      modelConfigured: boolean;
      storageHealth: { state: "ready" | "health_only"; code?: string };
    }
  | { state: "error"; message: string };

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
app.setName("Eidos");
let runtimeStatus: RuntimeStatus = { state: "starting" };
let runtimeClient: RuntimeClient | undefined;
let isQuitting = false;
let quitCanContinue = false;
let shutdownStarted = false;
const hasSingleInstanceLock = app.requestSingleInstanceLock();
const pendingApprovals = new Map<
  string,
  { request: ApprovalRequest; resolve: (decision: ApprovalDecision) => void }
>();

function publishStatus(status: RuntimeStatus): void {
  runtimeStatus = status;
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("runtime:status", status);
  }
}

function publishNotification(notification: RuntimeNotification): void {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("runtime:notification", notification);
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
      window.webContents.send("approval:requested", request);
    }
  });
}

function clientOrThrow(): RuntimeClient {
  if (!runtimeClient || runtimeStatus.state !== "ready") {
    throw new Error("Runtime 尚未就绪。");
  }
  return runtimeClient;
}

function createWindow(): void {
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
}

async function startRuntime(): Promise<void> {
  const runtimeRoot = path.join(app.getAppPath(), "runtime");
  const client = new RuntimeClient({
    pythonExecutable: process.env.EIDOS_PYTHON
      ?? path.join(app.getAppPath(), ".venv", "bin", "python"),
    runtimeRoot,
    dataDirectory: process.env.EIDOS_DATA_DIR ?? path.join(app.getPath("home"), ".eidos"),
    onNotification: publishNotification,
    onApprovalRequest: requestApproval,
    onStderr: (line) => console.error(`[runtime] ${line}`),
  });
  runtimeClient = client;

  void client.waitForExit().then((code) => {
    if (!isQuitting && runtimeStatus.state !== "error") {
      publishStatus({
        state: "error",
        message: `Runtime exited unexpectedly (code ${code}).`,
      });
    }
  });

  try {
    const initialized = await client.initialize();
    const storageHealth = await client.health();
    publishStatus({
      state: "ready",
      protocolVersion: initialized.protocolVersion,
      runtimeVersion: initialized.runtimeVersion,
      runShell: initialized.capabilities.runShell,
      modelConfigured: initialized.capabilities.modelConfigured,
      storageHealth,
    });
  } catch (error) {
    console.error("[runtime] initialization failed", error);
    publishStatus({
      state: "error",
      message: "Python Runtime 无法启动，请查看启动终端中的诊断日志。",
    });
  }
}

ipcMain.handle("runtime:get-status", () => runtimeStatus);
ipcMain.handle("runtime:health", () => clientOrThrow().health());
ipcMain.handle("workspace:select", async () => {
  const result = await dialog.showOpenDialog({
    title: "选择 Eidos Workspace",
    properties: ["openDirectory"],
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});
ipcMain.handle("session:list", () => clientOrThrow().listSessions());
ipcMain.handle("session:read", (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") {
    throw new Error("Session 参数无效。");
  }
  return clientOrThrow().readSession(sessionId);
});
ipcMain.handle("event:list", (_event, sessionId: unknown, afterEventId: unknown) => {
  if (typeof sessionId !== "string" || typeof afterEventId !== "number") {
    throw new Error("Event 参数无效。");
  }
  return clientOrThrow().listEvents(sessionId, afterEventId);
});
ipcMain.handle("session:create", (_event, workspaceRoot: unknown) => {
  if (typeof workspaceRoot !== "string") {
    throw new Error("Workspace 参数无效。");
  }
  return clientOrThrow().createSession(workspaceRoot);
});
ipcMain.handle("session:rename", (_event, sessionId: unknown, title: unknown) => {
  if (typeof sessionId !== "string" || typeof title !== "string") {
    throw new Error("Session 参数无效。");
  }
  return clientOrThrow().renameSession(sessionId, title);
});
ipcMain.handle("session:delete", (_event, sessionId: unknown) => {
  if (typeof sessionId !== "string") {
    throw new Error("Session 参数无效。");
  }
  return clientOrThrow().deleteSession(sessionId);
});
ipcMain.handle(
  "run:start",
  (_event, sessionId: unknown, userInput: unknown, modelId: unknown) => {
    if (
      typeof sessionId !== "string"
      || typeof userInput !== "string"
      || !["deepseek-v4-flash", "deepseek-v4-pro"].includes(String(modelId))
    ) {
      throw new Error("Run 参数无效。");
    }
    return clientOrThrow().startRun(
      sessionId,
      userInput,
      modelId as "deepseek-v4-flash" | "deepseek-v4-pro",
    );
  },
);
ipcMain.handle("run:cancel", (_event, runId: unknown) => {
  if (typeof runId !== "string") {
    throw new Error("Run 参数无效。");
  }
  return clientOrThrow().cancelRun(runId);
});
ipcMain.handle("run:continue", (_event, runId: unknown, userInput: unknown) => {
  if (typeof runId !== "string" || typeof userInput !== "string") {
    throw new Error("Run 参数无效。");
  }
  return clientOrThrow().continueRun(runId, userInput);
});
ipcMain.handle("model:status", () => clientOrThrow().modelStatus());
ipcMain.handle("model:list", () => clientOrThrow().listModels());
ipcMain.handle("model:configure", (_event, apiKey: unknown) => {
  if (typeof apiKey !== "string") {
    throw new Error("API Key 参数无效。");
  }
  return clientOrThrow().configureModel(apiKey);
});
ipcMain.handle("approval:list", () =>
  Array.from(pendingApprovals.values(), ({ request }) => request),
);
ipcMain.handle(
  "approval:respond",
  (
    _event,
    id: unknown,
    decision: unknown,
    feedback: unknown,
  ) => {
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
    if (!pending) {
      return false;
    }
    pendingApprovals.delete(id);
    const result: ApprovalDecision = decision === "approve"
      ? { decision: "approve" }
      : feedback
        ? { decision: "reject", feedback }
        : { decision: "reject" };
    pending.resolve(result);
    return true;
  },
);

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const window = BrowserWindow.getAllWindows()[0];
    if (!window) {
      return;
    }
    if (window.isMinimized()) {
      window.restore();
    }
    window.show();
    window.focus();
  });

  app.whenReady().then(() => {
    createWindow();
    void startRuntime();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });
}

app.on("before-quit", (event) => {
  if (!runtimeClient || quitCanContinue) {
    return;
  }
  event.preventDefault();
  isQuitting = true;
  if (shutdownStarted) {
    return;
  }
  shutdownStarted = true;

  const client = runtimeClient;
  const forceStop = new Promise<void>((resolve) => {
    setTimeout(() => {
      client.terminate();
      resolve();
    }, 8000);
  });
  const gracefulStop = client
    .shutdown()
    .then(() => client.waitForExit())
    .then(() => undefined)
    .catch(() => client.terminate());

  void Promise.race([gracefulStop, forceStop]).finally(() => {
    quitCanContinue = true;
    app.quit();
  });
});

app.on("will-quit", () => runtimeClient?.terminate());

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
