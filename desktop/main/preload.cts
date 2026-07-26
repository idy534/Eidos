import { contextBridge, ipcRenderer } from "electron";

// Import type only from shared — no runtime imports in CJS preload
import type { RuntimeStatus, RuntimeHealth } from "../shared/ipc-contracts.js";

// Channel names duplicated as string literals here so the CJS preload
// doesn't need a runtime import from shared. They must stay in sync
// with the IPC constant object in ipc-contracts.ts (validated via main.ts).
const CH = {
  RUNTIME_GET_STATUS: "runtime:get-status",
  RUNTIME_HEALTH: "runtime:health",
  RUNTIME_STATUS_EVENT: "runtime:status",
  RUNTIME_NOTIFICATION_EVENT: "runtime:notification",
  WORKSPACE_SELECT: "workspace:select",
  SESSION_LIST: "session:list",
  SESSION_READ: "session:read",
  SESSION_CREATE: "session:create",
  SESSION_RENAME: "session:rename",
  SESSION_DELETE: "session:delete",
  EVENT_LIST: "event:list",
  RUN_START: "run:start",
  RUN_CANCEL: "run:cancel",
  RUN_CONTINUE: "run:continue",
  MODEL_STATUS: "model:status",
  MODEL_LIST: "model:list",
  MODEL_CONFIGURE: "model:configure",
  PLUGIN_LIST: "plugin:list",
  PLUGIN_IMPORT: "plugin:import",
  PLUGIN_SET_ENABLED: "plugin:set-enabled",
  PLUGIN_REMOVE: "plugin:remove",
  SKILL_LIST: "skill:list",
  MCP_LIST: "mcp:list",
  MCP_SET_ENABLED: "mcp:set-enabled",
  EXTENSION_READ: "extension:read",
  EXTENSION_READ_EVENTS: "extension:read-events",
  APPROVAL_LIST: "approval:list",
  APPROVAL_RESPOND: "approval:respond",
  APPROVAL_REQUESTED_EVENT: "approval:requested",
  APP_NEW_TASK: "app:new-task",
  APP_OPEN_WORKSPACE: "app:open-workspace",
} as const;

// ---------------------------------------------------------------------------
// Type aliases kept inline so preload doesn't depend on renderer/src.
// These types MUST match the definitions in contracts.ts — validated by their
// usage in TypeScript strict mode when building the renderer.
// ---------------------------------------------------------------------------

type ModelId = "deepseek-v4-flash" | "deepseek-v4-pro";

contextBridge.exposeInMainWorld("eidosRuntime", {
  // Runtime status
  getStatus: (): Promise<RuntimeStatus> => ipcRenderer.invoke(CH.RUNTIME_GET_STATUS),
  getHealth: (): Promise<RuntimeHealth> => ipcRenderer.invoke(CH.RUNTIME_HEALTH),
  onStatus: (callback: (status: RuntimeStatus) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, status: RuntimeStatus) => callback(status);
    ipcRenderer.on(CH.RUNTIME_STATUS_EVENT, listener);
    return () => ipcRenderer.removeListener(CH.RUNTIME_STATUS_EVENT, listener);
  },

  // Workspace
  selectWorkspace: (): Promise<string | null> => ipcRenderer.invoke(CH.WORKSPACE_SELECT),

  // Sessions
  listSessions: (): Promise<unknown> => ipcRenderer.invoke(CH.SESSION_LIST),
  readSession: (sessionId: string): Promise<unknown> => ipcRenderer.invoke(CH.SESSION_READ, sessionId),
  listEvents: (sessionId: string, afterEventId: number): Promise<unknown> =>
    ipcRenderer.invoke(CH.EVENT_LIST, sessionId, afterEventId),
  createSession: (workspaceRoot: string): Promise<unknown> =>
    ipcRenderer.invoke(CH.SESSION_CREATE, workspaceRoot),
  renameSession: (sessionId: string, title: string): Promise<unknown> =>
    ipcRenderer.invoke(CH.SESSION_RENAME, sessionId, title),
  deleteSession: (sessionId: string): Promise<unknown> =>
    ipcRenderer.invoke(CH.SESSION_DELETE, sessionId),

  // Runs
  startRun: (sessionId: string, userInput: string, modelId: ModelId): Promise<unknown> =>
    ipcRenderer.invoke(CH.RUN_START, sessionId, userInput, modelId),
  cancelRun: (runId: string): Promise<unknown> => ipcRenderer.invoke(CH.RUN_CANCEL, runId),
  continueRun: (runId: string, userInput: string): Promise<unknown> =>
    ipcRenderer.invoke(CH.RUN_CONTINUE, runId, userInput),

  // Models
  getModelStatus: (): Promise<unknown> => ipcRenderer.invoke(CH.MODEL_STATUS),
  listModels: (): Promise<unknown> => ipcRenderer.invoke(CH.MODEL_LIST),
  configureModel: (apiKey: string): Promise<unknown> => ipcRenderer.invoke(CH.MODEL_CONFIGURE, apiKey),

  // Plugins
  listPlugins: (): Promise<unknown> => ipcRenderer.invoke(CH.PLUGIN_LIST),
  importPlugin: (): Promise<unknown> => ipcRenderer.invoke(CH.PLUGIN_IMPORT),
  setPluginEnabled: (pluginId: string, enabled: boolean): Promise<unknown> =>
    ipcRenderer.invoke(CH.PLUGIN_SET_ENABLED, pluginId, enabled),
  removePlugin: (pluginId: string): Promise<unknown> => ipcRenderer.invoke(CH.PLUGIN_REMOVE, pluginId),

  // Skills
  listSkills: (): Promise<unknown> => ipcRenderer.invoke(CH.SKILL_LIST),

  // MCP
  listMcpServers: (): Promise<unknown> => ipcRenderer.invoke(CH.MCP_LIST),
  setMcpEnabled: (pluginId: string, serverId: string, enabled: boolean): Promise<unknown> =>
    ipcRenderer.invoke(CH.MCP_SET_ENABLED, pluginId, serverId, enabled),

  // Extensions
  readExtensions: (): Promise<unknown> => ipcRenderer.invoke(CH.EXTENSION_READ),
  readExtensionEvents: (afterEventId: number): Promise<unknown> =>
    ipcRenderer.invoke(CH.EXTENSION_READ_EVENTS, afterEventId),

  // Approvals
  listPendingApprovals: (): Promise<unknown> => ipcRenderer.invoke(CH.APPROVAL_LIST),
  onApprovalRequest: (callback: (request: unknown) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, request: unknown) => callback(request);
    ipcRenderer.on(CH.APPROVAL_REQUESTED_EVENT, listener);
    return () => ipcRenderer.removeListener(CH.APPROVAL_REQUESTED_EVENT, listener);
  },
  respondApproval: (id: string, decision: "approve" | "reject", feedback?: string): Promise<boolean> =>
    ipcRenderer.invoke(CH.APPROVAL_RESPOND, id, decision, feedback),

  // Notifications
  onNotification: (callback: (notification: unknown) => void): (() => void) => {
    const listener = (_event: Electron.IpcRendererEvent, notification: unknown) => callback(notification);
    ipcRenderer.on(CH.RUNTIME_NOTIFICATION_EVENT, listener);
    return () => ipcRenderer.removeListener(CH.RUNTIME_NOTIFICATION_EVENT, listener);
  },

  // Shortcuts sent from Main process menu (from keyboard shortcuts)
  onShortcut: (channel: "app:new-task" | "app:open-workspace", callback: () => void): (() => void) => {
    const listener = () => callback();
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  },
});
