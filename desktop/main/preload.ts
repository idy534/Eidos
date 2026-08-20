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
} from "../shared/domain-contracts.js";
import type {
  ItemFeedbackResult,
  ResponseActionState,
  ResponseFeedbackValue,
  RunRevisionResult,
} from "../shared/response-actions.js";

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
  listWorkspaceDirectory: (
    sessionId: string,
    path: string,
    limit?: number,
  ): Promise<WorkspaceDirectoryListing> =>
    ipcRenderer.invoke(IPC.WORKSPACE_LIST_DIRECTORY, sessionId, path, limit),
  readWorkspaceFilePreview: (
    sessionId: string,
    path: string,
  ): Promise<WorkspaceFilePreview> =>
    ipcRenderer.invoke(IPC.WORKSPACE_READ_FILE_PREVIEW, sessionId, path),
  openWorkspacePathInEditor: (sessionId: string, path: string): Promise<void> =>
    ipcRenderer.invoke(IPC.WORKSPACE_OPEN_IN_EDITOR, sessionId, path),

  // User terminal
  createTerminal: (sessionId: string): Promise<TerminalSessionInfo> =>
    ipcRenderer.invoke(IPC.TERMINAL_CREATE, sessionId),
  writeTerminal: (terminalId: string, data: string): Promise<void> =>
    ipcRenderer.invoke(IPC.TERMINAL_WRITE, terminalId, data),
  resizeTerminal: (terminalId: string, columns: number, rows: number): Promise<void> =>
    ipcRenderer.invoke(IPC.TERMINAL_RESIZE, terminalId, columns, rows),
  closeTerminal: (terminalId: string): Promise<void> =>
    ipcRenderer.invoke(IPC.TERMINAL_CLOSE, terminalId),
  onTerminalData: (callback: (event: TerminalDataEvent) => void): Unsubscribe => {
    const listener = (_event: Electron.IpcRendererEvent, terminalEvent: TerminalDataEvent) =>
      callback(terminalEvent);
    ipcRenderer.on(IPC.TERMINAL_DATA_EVENT, listener);
    return () => ipcRenderer.removeListener(IPC.TERMINAL_DATA_EVENT, listener);
  },
  onTerminalExit: (callback: (event: TerminalExitEvent) => void): Unsubscribe => {
    const listener = (_event: Electron.IpcRendererEvent, terminalEvent: TerminalExitEvent) =>
      callback(terminalEvent);
    ipcRenderer.on(IPC.TERMINAL_EXIT_EVENT, listener);
    return () => ipcRenderer.removeListener(IPC.TERMINAL_EXIT_EVENT, listener);
  },

  // Sessions
  createProject: (name: string | undefined, workspaceRoot: string): Promise<Project> =>
    ipcRenderer.invoke(IPC.PROJECT_CREATE, name, workspaceRoot),
  listProjects: (): Promise<ProjectListResult> => ipcRenderer.invoke(IPC.PROJECT_LIST),
  deleteProject: (projectId: string): Promise<DeleteProjectResult> =>
    ipcRenderer.invoke(IPC.PROJECT_DELETE, projectId),
  listSessions: (): Promise<SessionListResult> => ipcRenderer.invoke(IPC.SESSION_LIST),
  readSession: (sessionId: string): Promise<SessionSnapshot> => ipcRenderer.invoke(IPC.SESSION_READ, sessionId),
  listEvents: (sessionId: string, afterEventId: number): Promise<EventListResult> =>
    ipcRenderer.invoke(IPC.EVENT_LIST, sessionId, afterEventId),
  createSession: (
    workspaceRoot: string | null,
    options?: {
      executionMode?: "local" | "worktree";
      baseRef?: string;
      includeLocalChanges?: boolean;
    },
  ): Promise<Session> => ipcRenderer.invoke(IPC.SESSION_CREATE, workspaceRoot, options),
  createSessionBranch: (sessionId: string, branch: string): Promise<CreateBranchResult> =>
    ipcRenderer.invoke(IPC.SESSION_CREATE_BRANCH, sessionId, branch),
  handoffSession: (
    sessionId: string,
    target: "local" | "worktree",
  ): Promise<SessionHandoffResult> => ipcRenderer.invoke(IPC.SESSION_HANDOFF, sessionId, target),
  restoreSessionWorktree: (sessionId: string): Promise<SessionRestoreWorktreeResult> =>
    ipcRenderer.invoke(IPC.SESSION_RESTORE_WORKTREE, sessionId),
  readWorktreeSettings: (): Promise<WorktreeSettings> =>
    ipcRenderer.invoke(IPC.WORKTREE_SETTINGS_READ),
  updateWorktreeSettings: (input: {
    automaticCleanup: boolean;
    managedWorktreeLimit: number;
  }): Promise<WorktreeSettings> => ipcRenderer.invoke(IPC.WORKTREE_SETTINGS_UPDATE, input),
  readProjectGitContext: (workspaceRoot: string): Promise<ProjectGitContext> =>
    ipcRenderer.invoke(IPC.PROJECT_GIT_CONTEXT, workspaceRoot),
  renameSession: (sessionId: string, title: string): Promise<Session> =>
    ipcRenderer.invoke(IPC.SESSION_RENAME, sessionId, title),
  deleteSession: (sessionId: string): Promise<DeleteSessionResult> =>
    ipcRenderer.invoke(IPC.SESSION_DELETE, sessionId),
  readSessionGitStatus: (sessionId: string): Promise<SessionGitStatus> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_STATUS, sessionId),
  readSessionGitDiff: (
    sessionId: string,
    scope: GitDiffScope,
    path?: string,
    compareRef?: string,
  ): Promise<SessionGitDiff> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_DIFF, sessionId, scope, path, compareRef),
  switchSessionGitBranch: (
    sessionId: string,
    branch: string,
    operationId: string,
  ): Promise<SessionGitMutationResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_SWITCH_BRANCH, sessionId, branch, operationId),
  createSessionGitBranch: (
    sessionId: string,
    branch: string,
    operationId: string,
  ): Promise<SessionGitMutationResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_CREATE_BRANCH, sessionId, branch, operationId),
  stageSessionGit: (
    sessionId: string,
    paths: string[],
    operationId: string,
  ): Promise<SessionGitMutationResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_STAGE, sessionId, paths, operationId),
  unstageSessionGit: (
    sessionId: string,
    paths: string[],
    operationId: string,
  ): Promise<SessionGitMutationResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_UNSTAGE, sessionId, paths, operationId),
  commitSessionGit: (
    sessionId: string,
    message: string,
    operationId: string,
  ): Promise<SessionGitCommitResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_COMMIT, sessionId, message, operationId),
  discardSessionGit: (
    sessionId: string,
    path: string,
    operationId: string,
  ): Promise<SessionGitDiscardResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_DISCARD, sessionId, path, operationId),
  listReviewComments: (
    sessionId: string,
    path?: string,
    scope?: GitDiffScope,
  ): Promise<ReviewComment[]> =>
    ipcRenderer.invoke(IPC.REVIEW_LIST_COMMENTS, sessionId, path, scope),
  createReviewComment: (
    sessionId: string,
    input: ReviewCommentCreateInput,
    operationId: string,
  ): Promise<ReviewComment> =>
    ipcRenderer.invoke(IPC.REVIEW_CREATE_COMMENT, sessionId, input, operationId),
  deleteReviewComment: (
    sessionId: string,
    commentId: string,
    operationId: string,
  ): Promise<string> =>
    ipcRenderer.invoke(IPC.REVIEW_DELETE_COMMENT, sessionId, commentId, operationId),
  readSessionGitRemoteStatus: (sessionId: string): Promise<GitRemoteStatus> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_REMOTE_STATUS, sessionId),
  fetchSessionGit: (
    sessionId: string,
    operationId: string,
    remote?: string,
  ): Promise<GitFetchResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_FETCH, sessionId, operationId, remote),
  pullSessionGit: (
    sessionId: string,
    operationId: string,
  ): Promise<GitPullResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_PULL, sessionId, operationId),
  pushSessionGit: (
    sessionId: string,
    operationId: string,
    remote?: string,
  ): Promise<GitPushResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_PUSH, sessionId, operationId, remote),
  mergeSessionGit: (
    sessionId: string,
    target: string,
    operationId: string,
  ): Promise<GitMergeResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_MERGE, sessionId, target, operationId),
  abortSessionGitMerge: (
    sessionId: string,
    operationId: string,
  ): Promise<GitMergeResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_MERGE_ABORT, sessionId, operationId),
  rebaseSessionGit: (
    sessionId: string,
    target: string,
    operationId: string,
  ): Promise<GitRebaseResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_REBASE, sessionId, target, operationId),
  continueSessionGitRebase: (
    sessionId: string,
    operationId: string,
  ): Promise<GitRebaseResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_REBASE_CONTINUE, sessionId, operationId),
  abortSessionGitRebase: (
    sessionId: string,
    operationId: string,
  ): Promise<GitRebaseResult> =>
    ipcRenderer.invoke(IPC.SESSION_GIT_REBASE_ABORT, sessionId, operationId),

  // Runs
  startRun: (sessionId: string, userInput: string, modelId: ModelId): Promise<Run> =>
    ipcRenderer.invoke(IPC.RUN_START, sessionId, userInput, modelId),
  cancelRun: (runId: string): Promise<Run> => ipcRenderer.invoke(IPC.RUN_CANCEL, runId),
  readContextUsage: (runId: string): Promise<ContextUsage | null> =>
    ipcRenderer.invoke(IPC.CONTEXT_USAGE, runId),
  reviseRun: (sourceRunId: string, userInput?: string): Promise<RunRevisionResult> =>
    ipcRenderer.invoke(IPC.RUN_REVISE, sourceRunId, userInput),

  // Response actions
  readResponseActionState: (sessionId: string): Promise<ResponseActionState> =>
    ipcRenderer.invoke(IPC.RESPONSE_ACTION_STATE, sessionId),
  setItemFeedback: (
    itemId: string,
    feedback: ResponseFeedbackValue | null,
  ): Promise<ItemFeedbackResult> => ipcRenderer.invoke(IPC.ITEM_SET_FEEDBACK, itemId, feedback),

  // Models
  listModelPresets: (): Promise<ModelPresetsResult> => ipcRenderer.invoke(IPC.MODEL_PRESETS),
  listModels: (): Promise<ModelListResult> => ipcRenderer.invoke(IPC.MODEL_LIST),
  createModel: (input: ModelCreateInput): Promise<ModelOption> =>
    ipcRenderer.invoke(IPC.MODEL_CREATE, input),
  updateModel: (input: ModelUpdateInput): Promise<ModelOption> =>
    ipcRenderer.invoke(IPC.MODEL_UPDATE, input),
  deleteModel: (id: ModelId): Promise<void> => ipcRenderer.invoke(IPC.MODEL_DELETE, id),

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
