import type {
  RuntimeStatus,
  RuntimeHealth,
  SessionListResult,
  SessionSnapshot,
  EventListResult,
  Session,
  SessionHandoffResult,
  SessionRestoreWorktreeResult,
  Project,
  ProjectListResult,
  DeleteProjectResult,
  WorktreeSettings,
  ProjectGitContext,
  CreateBranchResult,
  DeleteSessionResult,
  GitDiffScope,
  SessionGitDiff,
  SessionGitStatus,
  SessionGitMutationResult,
  SessionGitCommitResult,
  SessionGitDiscardResult,
  ReviewComment,
  ReviewCommentCreateInput,
  GitRemoteStatus,
  GitFetchResult,
  GitPullResult,
  GitPushResult,
  GitMergeResult,
  GitRebaseResult,
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
  WorkspaceDirectoryListing,
  WorkspaceFilePreview,
  TerminalSessionInfo,
  TerminalDataEvent,
  TerminalExitEvent,
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
  restartRuntime(): Promise<RuntimeStatus>;

  // Workspace
  selectWorkspace(): Promise<string | null>;
  listWorkspaceDirectory(
    sessionId: string,
    path: string,
    limit?: number,
  ): Promise<WorkspaceDirectoryListing>;
  readWorkspaceFilePreview(
    sessionId: string,
    path: string,
  ): Promise<WorkspaceFilePreview>;
  openWorkspacePathInEditor(sessionId: string, path: string): Promise<void>;

  // User terminal
  createTerminal(sessionId: string): Promise<TerminalSessionInfo>;
  writeTerminal(terminalId: string, data: string): Promise<void>;
  resizeTerminal(terminalId: string, columns: number, rows: number): Promise<void>;
  closeTerminal(terminalId: string): Promise<void>;
  onTerminalData(callback: (event: TerminalDataEvent) => void): Unsubscribe;
  onTerminalExit(callback: (event: TerminalExitEvent) => void): Unsubscribe;

  // Sessions
  createProject(name: string | undefined, workspaceRoot: string): Promise<Project>;
  listProjects(): Promise<ProjectListResult>;
  deleteProject(projectId: string): Promise<DeleteProjectResult>;
  listSessions(): Promise<SessionListResult>;
  readSession(sessionId: string): Promise<SessionSnapshot>;
  listEvents(sessionId: string, afterEventId: number): Promise<EventListResult>;
  createSession(
    workspaceRoot: string | null,
    options?: {
      executionMode?: "local" | "worktree";
      baseRef?: string;
      includeLocalChanges?: boolean;
    },
  ): Promise<Session>;
  createSessionBranch(sessionId: string, branch: string): Promise<CreateBranchResult>;
  handoffSession(
    sessionId: string,
    target: "local" | "worktree",
  ): Promise<SessionHandoffResult>;
  restoreSessionWorktree(sessionId: string): Promise<SessionRestoreWorktreeResult>;
  readWorktreeSettings(): Promise<WorktreeSettings>;
  updateWorktreeSettings(input: {
    automaticCleanup: boolean;
    managedWorktreeLimit: number;
  }): Promise<WorktreeSettings>;
  readProjectGitContext(workspaceRoot: string): Promise<ProjectGitContext>;
  renameSession(sessionId: string, title: string): Promise<Session>;
  deleteSession(sessionId: string): Promise<DeleteSessionResult>;
  readSessionGitStatus(sessionId: string): Promise<SessionGitStatus>;
  readSessionGitDiff(
    sessionId: string,
    scope: GitDiffScope,
    path?: string,
    compareRef?: string,
  ): Promise<SessionGitDiff>;
  switchSessionGitBranch(
    sessionId: string,
    branch: string,
    operationId: string,
  ): Promise<SessionGitMutationResult>;
  createSessionGitBranch(
    sessionId: string,
    branch: string,
    operationId: string,
  ): Promise<SessionGitMutationResult>;
  stageSessionGit(
    sessionId: string,
    paths: string[],
    operationId: string,
  ): Promise<SessionGitMutationResult>;
  unstageSessionGit(
    sessionId: string,
    paths: string[],
    operationId: string,
  ): Promise<SessionGitMutationResult>;
  commitSessionGit(
    sessionId: string,
    message: string,
    operationId: string,
  ): Promise<SessionGitCommitResult>;
  discardSessionGit(
    sessionId: string,
    path: string,
    operationId: string,
  ): Promise<SessionGitDiscardResult>;
  listReviewComments(
    sessionId: string,
    path?: string,
    scope?: GitDiffScope,
  ): Promise<ReviewComment[]>;
  createReviewComment(
    sessionId: string,
    input: ReviewCommentCreateInput,
    operationId: string,
  ): Promise<ReviewComment>;
  deleteReviewComment(
    sessionId: string,
    commentId: string,
    operationId: string,
  ): Promise<string>;
  readSessionGitRemoteStatus(sessionId: string): Promise<GitRemoteStatus>;
  fetchSessionGit(
    sessionId: string,
    operationId: string,
    remote?: string,
  ): Promise<GitFetchResult>;
  pullSessionGit(sessionId: string, operationId: string): Promise<GitPullResult>;
  pushSessionGit(
    sessionId: string,
    operationId: string,
    remote?: string,
  ): Promise<GitPushResult>;
  mergeSessionGit(
    sessionId: string,
    target: string,
    operationId: string,
  ): Promise<GitMergeResult>;
  abortSessionGitMerge(
    sessionId: string,
    operationId: string,
  ): Promise<GitMergeResult>;
  rebaseSessionGit(
    sessionId: string,
    target: string,
    operationId: string,
  ): Promise<GitRebaseResult>;
  continueSessionGitRebase(
    sessionId: string,
    operationId: string,
  ): Promise<GitRebaseResult>;
  abortSessionGitRebase(
    sessionId: string,
    operationId: string,
  ): Promise<GitRebaseResult>;

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
