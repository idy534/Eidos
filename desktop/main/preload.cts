import { contextBridge, ipcRenderer } from "electron";


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

contextBridge.exposeInMainWorld("eidosRuntime", {
  getStatus: (): Promise<RuntimeStatus> => ipcRenderer.invoke("runtime:get-status"),
  getHealth: (): Promise<unknown> => ipcRenderer.invoke("runtime:health"),
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
  listEvents: (sessionId: string, afterEventId: number): Promise<unknown> =>
    ipcRenderer.invoke("event:list", sessionId, afterEventId),
  createSession: (workspaceRoot: string): Promise<unknown> =>
    ipcRenderer.invoke("session:create", workspaceRoot),
  renameSession: (sessionId: string, title: string): Promise<unknown> =>
    ipcRenderer.invoke("session:rename", sessionId, title),
  deleteSession: (sessionId: string): Promise<unknown> =>
    ipcRenderer.invoke("session:delete", sessionId),
  startRun: (sessionId: string, userInput: string, modelId: string): Promise<unknown> =>
    ipcRenderer.invoke("run:start", sessionId, userInput, modelId),
  cancelRun: (runId: string): Promise<unknown> => ipcRenderer.invoke("run:cancel", runId),
  continueRun: (runId: string, userInput: string): Promise<unknown> =>
    ipcRenderer.invoke("run:continue", runId, userInput),
  getModelStatus: (): Promise<unknown> => ipcRenderer.invoke("model:status"),
  listModels: (): Promise<unknown> => ipcRenderer.invoke("model:list"),
  configureModel: (apiKey: string): Promise<unknown> =>
    ipcRenderer.invoke("model:configure", apiKey),
  listPlugins: (): Promise<unknown> => ipcRenderer.invoke("plugin:list"),
  importPlugin: (): Promise<unknown> => ipcRenderer.invoke("plugin:import"),
  setPluginEnabled: (pluginId: string, enabled: boolean): Promise<unknown> =>
    ipcRenderer.invoke("plugin:set-enabled", pluginId, enabled),
  removePlugin: (pluginId: string): Promise<unknown> =>
    ipcRenderer.invoke("plugin:remove", pluginId),
  listSkills: (): Promise<unknown> => ipcRenderer.invoke("skill:list"),
  listMcpServers: (): Promise<unknown> => ipcRenderer.invoke("mcp:list"),
  setMcpEnabled: (pluginId: string, serverId: string, enabled: boolean): Promise<unknown> =>
    ipcRenderer.invoke("mcp:set-enabled", pluginId, serverId, enabled),
  readExtensions: (): Promise<unknown> => ipcRenderer.invoke("extension:read"),
  readExtensionEvents: (afterEventId: number): Promise<unknown> =>
    ipcRenderer.invoke("extension:read-events", afterEventId),
  listPendingApprovals: (): Promise<unknown> => ipcRenderer.invoke("approval:list"),
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
