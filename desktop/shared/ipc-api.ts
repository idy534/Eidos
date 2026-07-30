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
  ModelProfile,
  ModelProfileDraft,
  ModelTestConnectionResult,
  ApprovalRequest,
  PluginListResult,
  PluginRecord,
  SkillListResult,
  McpListResult,
  McpServerRecord,
  ExtensionSnapshot,
  RuntimeNotification,
  AppShortcut,
} from "./domain-contracts.js";

export type Unsubscribe = () => void;

export interface EidosRuntimeAPI {
  // Runtime
  getStatus(): Promise<RuntimeStatus>;
  getHealth(): Promise<RuntimeHealth>;

  // Workspace
  selectWorkspace(): Promise<string | null>;

  // Sessions
  listSessions(): Promise<SessionListResult>;
  readSession(sessionId: string): Promise<SessionSnapshot>;
  listEvents(sessionId: string, afterEventId: number): Promise<EventListResult>;
  createSession(workspaceRoot: string): Promise<Session>;
  renameSession(sessionId: string, title: string): Promise<Session>;
  deleteSession(sessionId: string): Promise<DeleteSessionResult>;

  // Runs
  startRun(sessionId: string, userInput: string, modelId: ModelId): Promise<Run>;
  cancelRun(runId: string): Promise<Run>;

  // Models
  getModelStatus(): Promise<ModelStatus>;
  listModels(): Promise<ModelListResult>;
  configureModel(apiKey: string): Promise<ModelStatus>;
  listModelProfiles(): Promise<ModelProfile[]>;
  createModelProfile(profile: ModelProfileDraft, apiKey?: string): Promise<ModelProfile>;
  updateModelProfile(profileId: string, profile: ModelProfileDraft, apiKey?: string): Promise<ModelProfile>;
  deleteModelProfile(profileId: string): Promise<void>;
  testModelProfile(profileId: string): Promise<ModelTestConnectionResult>;

  // Approvals
  listPendingApprovals(): Promise<ApprovalRequest[]>;
  respondApproval(id: string, decision: "approve" | "reject", feedback?: string): Promise<boolean>;

  // Extensions
  listPlugins(): Promise<PluginListResult>;
  importPlugin(): Promise<PluginRecord | null>;
  setPluginEnabled(pluginId: string, enabled: boolean): Promise<PluginRecord>;
  removePlugin(pluginId: string): Promise<PluginRecord>;

  listSkills(): Promise<SkillListResult>;

  listMcpServers(): Promise<McpListResult>;
  setMcpEnabled(pluginId: string, serverId: string, enabled: boolean): Promise<McpServerRecord>;

  readExtensions(): Promise<ExtensionSnapshot>;
  readExtensionEvents(afterEventId: number): Promise<EventListResult>;

  // Events
  onStatus(callback: (status: RuntimeStatus) => void): Unsubscribe;
  onNotification(callback: (notification: RuntimeNotification) => void): Unsubscribe;
  onApprovalRequest(callback: (approval: ApprovalRequest) => void): Unsubscribe;
  onShortcut(shortcut: AppShortcut, callback: () => void): Unsubscribe;
}
