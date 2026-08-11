import type {
  RuntimeStatus,
  RuntimeHealth,
  SessionListResult,
  SessionSnapshot,
  EventListResult,
  Session,
  ProjectGitContext,
  DeleteSessionResult,
  GitDiffScope,
  SessionGitDiff,
  SessionGitStatus,
  Run,
  ContextUsage,
  ModelId,
  ModelListResult,
  ModelOption,
  ModelPresetsResult,
  ModelCreateInput,
  ModelUpdateInput,
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
import type {
  ItemFeedbackResult,
  ResponseActionState,
  ResponseFeedbackValue,
  RunRevisionResult,
} from "./response-actions.js";

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
  createSession(
    workspaceRoot: string,
    options?: { executionMode?: "local" | "worktree"; baseRef?: string },
  ): Promise<Session>;
  readProjectGitContext(workspaceRoot: string): Promise<ProjectGitContext>;
  renameSession(sessionId: string, title: string): Promise<Session>;
  deleteSession(sessionId: string): Promise<DeleteSessionResult>;
  readSessionGitStatus(sessionId: string): Promise<SessionGitStatus>;
  readSessionGitDiff(sessionId: string, scope: GitDiffScope): Promise<SessionGitDiff>;

  // Runs
  startRun(sessionId: string, userInput: string, modelId: ModelId): Promise<Run>;
  cancelRun(runId: string): Promise<Run>;
  readContextUsage(runId: string): Promise<ContextUsage | null>;
  reviseRun(sourceRunId: string, userInput?: string): Promise<RunRevisionResult>;

  // Response actions
  readResponseActionState(sessionId: string): Promise<ResponseActionState>;
  setItemFeedback(
    itemId: string,
    feedback: ResponseFeedbackValue | null,
  ): Promise<ItemFeedbackResult>;

  // Models
  listModelPresets(): Promise<ModelPresetsResult>;
  listModels(): Promise<ModelListResult>;
  createModel(input: ModelCreateInput): Promise<ModelOption>;
  updateModel(input: ModelUpdateInput): Promise<ModelOption>;
  deleteModel(id: ModelId): Promise<void>;

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
