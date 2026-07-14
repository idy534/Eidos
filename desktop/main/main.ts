import { app, BrowserWindow, ipcMain } from "electron";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { RuntimeClient } from "./runtime-client.js";


type RuntimeStatus =
  | { state: "starting" }
  | {
      state: "ready";
      protocolVersion: number;
      runtimeVersion: string;
      runShell: boolean;
    }
  | { state: "error"; message: string };

const currentDirectory = path.dirname(fileURLToPath(import.meta.url));
let runtimeStatus: RuntimeStatus = { state: "starting" };
let runtimeClient: RuntimeClient | undefined;
let isQuitting = false;
let quitCanContinue = false;
let shutdownStarted = false;

function publishStatus(status: RuntimeStatus): void {
  runtimeStatus = status;
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send("runtime:status", status);
  }
}

function createWindow(): void {
  const window = new BrowserWindow({
    width: 920,
    height: 640,
    minWidth: 640,
    minHeight: 480,
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
    pythonExecutable: process.env.EIDOS_PYTHON ?? "python3",
    runtimeRoot,
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
    publishStatus({
      state: "ready",
      protocolVersion: initialized.protocolVersion,
      runtimeVersion: initialized.runtimeVersion,
      runShell: initialized.capabilities.runShell,
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

app.whenReady().then(() => {
  createWindow();
  void startRuntime();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

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
    }, 2000);
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
