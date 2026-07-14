import { contextBridge, ipcRenderer } from "electron";


type RuntimeStatus =
  | { state: "starting" }
  | {
      state: "ready";
      protocolVersion: number;
      runtimeVersion: string;
      runShell: boolean;
      modelConfigured: boolean;
    }
  | { state: "error"; message: string };

contextBridge.exposeInMainWorld("eidosRuntime", {
  getStatus: (): Promise<RuntimeStatus> => ipcRenderer.invoke("runtime:get-status"),
  onStatus: (callback: (status: RuntimeStatus) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: RuntimeStatus) => {
      callback(status);
    };
    ipcRenderer.on("runtime:status", listener);
    return () => ipcRenderer.removeListener("runtime:status", listener);
  },
  selectWorkspace: (): Promise<string | null> => ipcRenderer.invoke("workspace:select"),
  listSessions: (): Promise<unknown> => ipcRenderer.invoke("session:list"),
  readSession: (sessionId: string): Promise<unknown> =>
    ipcRenderer.invoke("session:read", sessionId),
  createSession: (workspaceRoot: string): Promise<unknown> =>
    ipcRenderer.invoke("session:create", workspaceRoot),
  startRun: (sessionId: string, userInput: string): Promise<unknown> =>
    ipcRenderer.invoke("run:start", sessionId, userInput),
  cancelRun: (runId: string): Promise<unknown> => ipcRenderer.invoke("run:cancel", runId),
  getModelStatus: (): Promise<unknown> => ipcRenderer.invoke("model:status"),
  configureModel: (apiKey: string): Promise<unknown> =>
    ipcRenderer.invoke("model:configure", apiKey),
  onNotification: (callback: (notification: unknown) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, notification: unknown) => {
      callback(notification);
    };
    ipcRenderer.on("runtime:notification", listener);
    return () => ipcRenderer.removeListener("runtime:notification", listener);
  },
  onApprovalRequest: (callback: (request: unknown) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, request: unknown) => {
      callback(request);
    };
    ipcRenderer.on("approval:requested", listener);
    return () => ipcRenderer.removeListener("approval:requested", listener);
  },
  respondApproval: (
    id: string,
    decision: "approve" | "reject",
    feedback?: string,
  ): Promise<boolean> => ipcRenderer.invoke("approval:respond", id, decision, feedback),
});
