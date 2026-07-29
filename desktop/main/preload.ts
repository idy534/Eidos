import { contextBridge, ipcRenderer } from "electron";
import { IPC } from "../shared/index.js";
import type { EidosRuntimeAPI, Unsubscribe } from "../shared/ipc-api.js";
import type {
  RuntimeStatus,
  RuntimeHealth,
  SessionListResult,
  SessionSnapshot,
  EventListResult,
  Session,
  DeleteSessionResult,
  Run,
  ModelId,
  ModelStatus,
  ModelListResult,
  ApprovalRequest,
  PluginListResult,
  PluginRecord,
  SkillListResult,
  McpListResult,
  McpServerRecord,
  ExtensionSnapshot,
  RuntimeNotification,
  AppShortcut,
} from "../shared/domain-contracts.js";

const api: EidosRuntimeAPI = {
  // Runtime status
  getStatus: (): Promise<RuntimeStatus> => ipcRenderer.invoke(IPC.RUNTIME_GET_STATUS),
  getHealth: (): Promise<RuntimeHealth> => ipcRenderer.invoke(IPC.RUNTIME_HEALTH),
  onStatus: (callback: (status: RuntimeStatus) => void): Unsubscribe => {
    const listener = (_event: Electron.IpcRendererEvent, status: RuntimeStatus) => callback(status);
    ipcRenderer.on(IPC.RUNTIME_STATUS_EVENT, listener);
    return () => ipcRenderer.removeListener(IPC.RUNTIME_STATUS_EVENT, listener);
  },

  // Workspace
  selectWorkspace: (): Promise<string | null> => ipcRenderer.invoke(IPC.WORKSPACE_SELECT),

  // Sessions
  listSessions: (): Promise<SessionListResult> => ipcRenderer.invoke(IPC.SESSION_LIST),
  readSession: (sessionId: string): Promise<SessionSnapshot> => ipcRenderer.invoke(IPC.SESSION_READ, sessionId),
  listEvents: (sessionId: string, afterEventId: number): Promise<EventListResult> =>
    ipcRenderer.invoke(IPC.EVENT_LIST, sessionId, afterEventId),
  createSession: (workspaceRoot: string): Promise<Session> =>
    ipcRenderer.invoke(IPC.SESSION_CREATE, workspaceRoot),
  renameSession: (sessionId: string, title: string): Promise<Session> =>
    ipcRenderer.invoke(IPC.SESSION_RENAME, sessionId, title),
  deleteSession: (sessionId: string): Promise<DeleteSessionResult> =>
    ipcRenderer.invoke(IPC.SESSION_DELETE, sessionId),

  // Runs
  startRun: (sessionId: string, userInput: string, modelId: ModelId): Promise<Run> =>
    ipcRenderer.invoke(IPC.RUN_START, sessionId, userInput, modelId),
  cancelRun: (runId: string): Promise<Run> => ipcRenderer.invoke(IPC.RUN_CANCEL, runId),

  // Models
  getModelStatus: (): Promise<ModelStatus> => ipcRenderer.invoke(IPC.MODEL_STATUS),
  listModels: (): Promise<ModelListResult> => ipcRenderer.invoke(IPC.MODEL_LIST),
  configureModel: (apiKey: string): Promise<ModelStatus> => ipcRenderer.invoke(IPC.MODEL_CONFIGURE, apiKey),

  // Plugins
  listPlugins: (): Promise<PluginListResult> => ipcRenderer.invoke(IPC.PLUGIN_LIST),
  importPlugin: (): Promise<PluginRecord | null> => ipcRenderer.invoke(IPC.PLUGIN_IMPORT),
  setPluginEnabled: (pluginId: string, enabled: boolean): Promise<PluginRecord> =>
    ipcRenderer.invoke(IPC.PLUGIN_SET_ENABLED, pluginId, enabled),
  removePlugin: (pluginId: string): Promise<PluginRecord> => ipcRenderer.invoke(IPC.PLUGIN_REMOVE, pluginId),

  // Skills
  listSkills: (): Promise<SkillListResult> => ipcRenderer.invoke(IPC.SKILL_LIST),

  // MCP
  listMcpServers: (): Promise<McpListResult> => ipcRenderer.invoke(IPC.MCP_LIST),
  setMcpEnabled: (pluginId: string, serverId: string, enabled: boolean): Promise<McpServerRecord> =>
    ipcRenderer.invoke(IPC.MCP_SET_ENABLED, pluginId, serverId, enabled),

  // Extensions
  readExtensions: (): Promise<ExtensionSnapshot> => ipcRenderer.invoke(IPC.EXTENSION_READ),
  readExtensionEvents: (afterEventId: number): Promise<EventListResult> =>
    ipcRenderer.invoke(IPC.EXTENSION_READ_EVENTS, afterEventId),

  // Approvals
  listPendingApprovals: (): Promise<ApprovalRequest[]> => ipcRenderer.invoke(IPC.APPROVAL_LIST),
  onApprovalRequest: (callback: (request: ApprovalRequest) => void): Unsubscribe => {
    const listener = (_event: Electron.IpcRendererEvent, request: ApprovalRequest) => callback(request);
    ipcRenderer.on(IPC.APPROVAL_REQUESTED_EVENT, listener);
    return () => ipcRenderer.removeListener(IPC.APPROVAL_REQUESTED_EVENT, listener);
  },
  respondApproval: (id: string, decision: "approve" | "reject", feedback?: string): Promise<boolean> =>
    ipcRenderer.invoke(IPC.APPROVAL_RESPOND, id, decision, feedback),

  // Notifications
  onNotification: (callback: (notification: RuntimeNotification) => void): Unsubscribe => {
    const listener = (_event: Electron.IpcRendererEvent, notification: RuntimeNotification) => callback(notification);
    ipcRenderer.on(IPC.RUNTIME_NOTIFICATION_EVENT, listener);
    return () => ipcRenderer.removeListener(IPC.RUNTIME_NOTIFICATION_EVENT, listener);
  },

  // Shortcuts
  onShortcut: (shortcut: AppShortcut, callback: () => void): Unsubscribe => {
    const listener = () => callback();
    ipcRenderer.on(shortcut, listener);
    return () => ipcRenderer.removeListener(shortcut, listener);
  },
};

contextBridge.exposeInMainWorld("eidosRuntime", api);
