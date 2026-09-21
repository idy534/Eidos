import type { InputDraft, InputPrepareRequest, InputPreview, InputReference } from "./input-context.js";
import type {
  RuntimeStatus,
  RuntimeHealth,
  SessionListResult,
  SessionSnapshot,
  ToolTextPage,
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
  ModelReasoningSelection,
  ApprovalMode,
  ModelListResult,
  ModelOption,
  ModelPresetsResult,
  ModelCreateInput,
  ModelUpdateInput,
  ApprovalRequest,
  PluginListResult,
  PluginRecord,
  SkillListResult,
  SkillMetadata,
  SkillDetail,
  SkillRemoval,
  McpListResult,
  McpServerRecord,
  McpCreateInput,
  McpUpdateInput,
  McpServerRemoval,
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
    workspaceRoot?: string,
    projectId?: string,
  ): Promise<WorkspaceDirectoryListing>;
  prepareWorkspacePreview(
    sessionId: string,
    path: string,
    version?: string,
    workspaceRoot?: string,
    projectId?: string,
  ): Promise<string>;
  releaseWorkspacePreview(url: string): Promise<void>;
  openBrowser(
    sessionId: string,
    browserId: string,
    url: string,
    workspaceRoot?: string,
  ): Promise<import("./domain-contracts.js").BrowserPageState>;
  setBrowserBounds(sessionId: string, browserId: string, bounds: import("./domain-contracts.js").BrowserBounds | null): Promise<void>;
  closeBrowser(sessionId: string, browserId: string): Promise<void>;
  readBrowserState(sessionId: string, browserId: string): Promise<import("./domain-contracts.js").BrowserPageState>;
  annotateBrowser(sessionId: string, browserId: string): Promise<import("./domain-contracts.js").BrowserAnnotation>;
  readWorkspaceFilePreview(
    sessionId: string,
    path: string,
    workspaceRoot?: string,
    projectId?: string,
  ): Promise<WorkspaceFilePreview>;
  openWorkspacePathInEditor(sessionId: string, path: string): Promise<void>;
  showItemInFolder(path: string): Promise<void>;

  // User terminal
  createTerminal(sessionId: string, workspaceRoot?: string, projectId?: string): Promise<TerminalSessionInfo>;
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
  readSession(sessionId: string, options?: { itemLimit?: number; beforeItemId?: string }): Promise<SessionSnapshot>;
  readToolText(sessionId: string, toolCallId: string, field: "diff" | "result", sha256: string, offset?: number): Promise<ToolTextPage>;
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
  commitSessionGit(
    sessionId: string,
    message: string,
    operationId: string,
  ): Promise<SessionGitCommitResult>;
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

  onInputQuote(callback: (text: string) => void): Unsubscribe;
  pickInputPaths(directory?: boolean): Promise<string[]>;
  inputPathForFile(file: File): string;
  prepareInput(request: InputPrepareRequest): Promise<InputReference>;
  readInput(id: string): Promise<InputPreview>;
  pasteInputImage(): Promise<InputReference | null>;
  readInputDraft(key: string): Promise<InputDraft>;
  writeInputDraft(key: string, draft: InputDraft): Promise<InputDraft>;

  // Runs
  startRun(
    sessionId: string,
    userInput: string,
    modelId: ModelId,
    reasoningSelection?: ModelReasoningSelection,
    approvalMode?: ApprovalMode,
    references?: string[],
  ): Promise<Run>;
  cancelRun(runId: string): Promise<Run>;
  readContextUsage(runId: string): Promise<ContextUsage | null>;
  reviseRun(sourceRunId: string, userInput?: string, references?: string[]): Promise<RunRevisionResult>;

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
  readSkillDetail(qualifiedId: string): Promise<SkillDetail>;
  setSkillEnabled(qualifiedId: string, enabled: boolean): Promise<SkillMetadata>;
  removeSkill(qualifiedId: string): Promise<SkillRemoval>;

  listMcpServers(): Promise<McpListResult>;
  setMcpEnabled(pluginId: string, serverId: string, enabled: boolean): Promise<McpServerRecord>;
  createMcpServer(input: McpCreateInput): Promise<McpServerRecord>;
  updateMcpServer(input: McpUpdateInput): Promise<McpServerRecord>;
  removeMcpServer(serverId: string): Promise<McpServerRemoval>;

  readExtensions(): Promise<ExtensionSnapshot>;
  readExtensionEvents(afterEventId: number): Promise<EventListResult>;

  // Events
  onStatus(callback: (status: RuntimeStatus) => void): Unsubscribe;
  onNotification(callback: (notification: RuntimeNotification) => void): Unsubscribe;
  onApprovalRequest(callback: (approval: ApprovalRequest) => void): Unsubscribe;
  onShortcut(shortcut: AppShortcut, callback: () => void): Unsubscribe;
}
