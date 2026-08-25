import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import readline from "node:readline";


const MAX_MESSAGE_BYTES = 1024 * 1024;
const RUNTIME_BUSINESS_CODES = new Set([
  "RUNTIME_NOT_INITIALIZED",
  "RUNTIME_DRAINING",
  "RUNTIME_RECONFIGURING",
  "RUNTIME_SHUTDOWN_TIMEOUT",
  "PROTOCOL_VERSION_UNSUPPORTED",
  "INVALID_PARAMS",
  "INVALID_CURSOR",
  "INVALID_EVENT_CURSOR",
  "RUN_ALREADY_ACTIVE",
  "RESOURCE_NOT_FOUND",
  "INVALID_STATE",
  "APPROVAL_NO_LONGER_PENDING",
  "WORKSPACE_BOUNDARY_VIOLATION",
  "SANDBOX_UNAVAILABLE",
  "STORAGE_HEALTH_ONLY",
  "OPERATION_ID_REUSED",
  "OPERATION_IN_PROGRESS",
  "INTERNAL_ERROR",
  "SENSITIVE_CONTENT_REJECTED",
  "SENSITIVE_SCAN_FAILED",
  "INVALID_SESSION_TITLE",
  "SESSION_HAS_ACTIVE_RUN",
  "PROJECT_HAS_SESSIONS",
  "PROJECT_WORKTREE_RECOVERY_REQUIRED",
  "PROJECT_PERSISTENCE_FAILED",
  "HANDOFF_NOT_SUPPORTED",
  "HANDOFF_SOURCE_CHANGED",
  "HANDOFF_TARGET_CHANGED",
  "HANDOFF_LOCAL_CONFLICT",
  "HANDOFF_GIT_CONFLICT",
  "WORKTREE_RESTORE_REQUIRED",
  "REPOSITORY_NOT_FOUND",
  "NOT_A_GIT_REPOSITORY",
  "WORKTREE_REQUIRES_GIT",
  "BASE_REF_NOT_FOUND",
  "GIT_COMMAND_TIMEOUT",
  "GIT_COMPARE_REF_INVALID",
  "WORKTREE_CREATE_FAILED",
  "WORKTREE_PERSISTENCE_FAILED",
  "WORKTREE_RECOVERY_REQUIRED",
  "LOCAL_CHANGES_BASE_MISMATCH",
  "WORKTREE_SOURCE_CHANGED",
  "WORKTREE_LOCAL_CHANGES_CONFLICT",
  "WORKTREE_INCLUDE_INVALID",
  "WORKTREE_INCLUDE_FAILED",
  "WORKTREE_REQUIRED",
  "WORKTREE_ALREADY_ATTACHED",
  "BRANCH_ALREADY_EXISTS",
  "WORKTREE_BRANCH_IN_USE",
  "BRANCH_INVALID",
  "WORKTREE_BRANCH_CREATE_FAILED",
  "WORKTREE_BRANCH_STATE_CHANGED",
  "LOCAL_REQUIRED",
  "GIT_BRANCH_NOT_FOUND",
  "GIT_BRANCH_SWITCH_FAILED",
  "GIT_BRANCH_CREATE_FAILED",
  "WORKTREE_NOT_FOUND",
  "WORKTREE_INVALID",
  "WORKSPACE_IDENTITY_UNAVAILABLE",
  "WORKSPACE_BOUNDARY_VIOLATION",
  "WORKSPACE_SENSITIVE_PATH",
  "WORKSPACE_UNAVAILABLE",
  "WORKSPACE_IDENTITY_CHANGED",
  "WORKSPACE_READ_TIMEOUT",
  "WORKSPACE_FILE_TOO_LARGE",
  "WORKSPACE_SENSITIVE_CONTENT",
  "CHECKPOINT_GIT_STATE_UNAVAILABLE",
  "CHECKPOINT_FORK_WORKTREE_FAILED",
  "CHECKPOINT_REWIND_FAILED",
  "CHECKPOINT_WORKFLOW_BUSY",
  "DIRECT_CHECKPOINT_FORK_PATH_FORBIDDEN",
  "MANAGED_CHECKPOINT_FORK_PATH_FORBIDDEN",
  "ASYNC_OPERATION_CANCELED",
  "ASYNC_OPERATION_INTERRUPTED",
  "GIT_WORKTREE_NOT_MANAGED",
  "GIT_OBSERVATION_UNAVAILABLE",
  "GIT_WORKTREE_NOT_FOUND",
  "GIT_WORKTREE_MISSING",
  "GIT_WORKTREE_INVALID",
  "GIT_REVIEW_FAILED",
  "GIT_NOT_REPOSITORY",
  "GIT_BRANCH_REQUIRED",
  "GIT_WORKFLOW_BUSY",
  "GIT_INVALID_PATH",
  "GIT_DISCARD_REQUIRES_UNSTAGED",
  "GIT_NOTHING_STAGED",
  "GIT_IDENTITY_UNAVAILABLE",
  "GIT_CONFLICT",
  "GIT_COMMAND_FAILED",
  "GIT_REMOTE_REQUIRED",
  "GIT_OPERATION_IN_PROGRESS",
  "GIT_MERGE_NOT_IN_PROGRESS",
  "GIT_MERGE_TARGET_INVALID",
  "GIT_REBASE_NOT_IN_PROGRESS",
  "GIT_REBASE_TARGET_INVALID",
  "GIT_REMOTE_NOT_FOUND",
  "GIT_UPSTREAM_NOT_FOUND",
  "GIT_REMOTE_UNSUPPORTED",
  "GIT_REMOTE_TIMEOUT",
  "GIT_REMOTE_CANCELED",
  "GIT_REMOTE_FAILED",
  "GIT_REMOTE_OUTCOME_UNCERTAIN",
  "GIT_WORKTREE_DIRTY",
  "GIT_REMOTE_BEHIND",
  "GIT_REMOTE_DIVERGED",
  "REVIEW_DIFF_CHANGED",
  "REVIEW_ANCHOR_INVALID",
  "REVIEW_COMMENT_ID_REUSED",
  "REVIEW_COMMENT_NOT_FOUND",
  "WORKSPACE_IDENTITY_CHANGED",
  "WORKTREE_DIRTY",
  "WORKTREE_DELETE_FAILED",
  "SESSION_PERSISTENCE_FAILED",
  "MODEL_NOT_AVAILABLE",
  "RUN_CANCEL_TIMEOUT",
  "RUN_RECONCILIATION_REQUIRED",
  "EXTENSIONS_UNAVAILABLE",
  "PLUGIN_IMPORT_REJECTED",
  "PLUGIN_IMPORT_FAILED",
  "PLUGIN_VERSION_CONFLICT",
  "PLUGIN_ID_CONFLICT",
  "SKILL_CATALOG_UNAVAILABLE",
  "SKILL_UNAVAILABLE",
  "MCP_SERVER_DISABLED",
]);

import type {
  ApprovalDecision,
  ApprovalRequest,
  FileApprovalRequest,
  CommandApprovalRequest,
  ExternalToolApprovalRequest,
  NetworkApprovalRequest,
  EventListResult,
  Item,
  McpListResult,
  McpServerRecord,
  ModelId,
  ModelListResult,
  ModelOption,
  ModelPresetsResult,
  ModelCreateInput,
  ModelUpdateInput,
  PluginListResult,
  PluginRecord,
  Run,
  ContextUsage,
  RuntimeEvent,
  RuntimeHealth,
  RuntimeNotification,
  ProjectGitContext,
  Project,
  ProjectListResult,
  DeleteProjectResult,
  CreateBranchResult,
  Session,
  SessionHandoffResult,
  SessionRestoreWorktreeResult,
  WorktreeSettings,
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
  SessionListResult,
  SessionSnapshot,
  SkillListResult,
  SkillMetadata,
  ToolCall,
  ToolProvenance,
  ExtensionSnapshot as ExtensionSnapshotResult,
  WorkspaceDirectoryListing,
  WorkspaceFilePreview,
} from "../shared/index.js";

export interface InitializeResult {
  protocolVersion: number;
  runtimeVersion: string;
  capabilities: {
    runShell: boolean;
    modelConfigured: boolean;
  };
}

export type {
  ApprovalDecision,
  ApprovalRequest,
  FileApprovalRequest,
  CommandApprovalRequest,
  ExternalToolApprovalRequest,
  NetworkApprovalRequest,
  EventListResult,
  Item,
  McpListResult,
  McpServerRecord,
  ModelId,
  ModelListResult,
  ModelOption,
  ModelPresetsResult,
  ModelCreateInput,
  ModelUpdateInput,
  PluginListResult,
  PluginRecord,
  Run,
  ContextUsage,
  RuntimeEvent,
  RuntimeHealth,
  RuntimeNotification,
  Session,
  CreateBranchResult,
  SessionListResult,
  SessionSnapshot,
  SkillListResult,
  SkillMetadata,
  ToolCall,
  ToolProvenance,
  ExtensionSnapshotResult,
};

interface RuntimeClientOptions {
  pythonExecutable: string;
  runtimeRoot: string;
  dataDirectory?: string;
  environment?: NodeJS.ProcessEnv;
  environmentPolicy?: RuntimeEnvironmentPolicy;
  onNotification?: (notification: RuntimeNotification) => void;
  onApprovalRequest?: (request: ApprovalRequest) => Promise<ApprovalDecision>;
  onStderr?: (line: string) => void;
}

export type RuntimeEnvironmentPolicy = "development" | "packaged";

export interface RuntimeEnvironmentOptions {
  runtimeRoot: string;
  baseEnvironment: NodeJS.ProcessEnv;
  overrides?: NodeJS.ProcessEnv | undefined;
  policy: RuntimeEnvironmentPolicy;
}

export function buildRuntimeEnvironment(
  options: RuntimeEnvironmentOptions,
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {
    ...options.baseEnvironment,
    ...options.overrides,
  };
  if (options.policy === "packaged") {
    environment.PYTHONPATH = options.runtimeRoot;
    delete environment.PYTHONHOME;
    delete environment.EIDOS_PYTHON;
    environment.PYTHONNOUSERSITE = "1";
    environment.PYTHONDONTWRITEBYTECODE = "1";
    return environment;
  }

  environment.PYTHONPATH = [
    options.runtimeRoot,
    options.baseEnvironment.PYTHONPATH,
  ].filter((entry): entry is string => Boolean(entry)).join(path.delimiter);
  return environment;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timeout: ReturnType<typeof setTimeout>;
}

interface RpcError {
  code: number;
  message: string;
  data?: {
    code?: string;
    retryable?: boolean;
  };
}

export class RuntimeRequestError extends Error {
  readonly rpcCode: number;
  readonly businessCode: string | undefined;

  constructor(error: RpcError) {
    const businessCode = RUNTIME_BUSINESS_CODES.has(error.data?.code ?? "")
      ? error.data?.code
      : "INTERNAL_ERROR";
    super(`EIDOS_RUNTIME_ERROR:${businessCode}`);
    this.name = "RuntimeRequestError";
    this.rpcCode = error.code;
    this.businessCode = businessCode;
  }
}

export class RuntimeClient {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly pending = new Map<string, PendingRequest>();
  private readonly exitPromise: Promise<number>;
  private readonly onNotification: ((notification: RuntimeNotification) => void) | undefined;
  private readonly onApprovalRequest: ((request: ApprovalRequest) => Promise<ApprovalDecision>) | undefined;
  private stdoutBuffer = Buffer.alloc(0);
  private nextRequestId = 1;
  private closed = false;

  constructor(options: RuntimeClientOptions) {
    const environment = buildRuntimeEnvironment({
      runtimeRoot: options.runtimeRoot,
      baseEnvironment: process.env,
      overrides: options.environment,
      policy: options.environmentPolicy ?? "development",
    });
    if (options.dataDirectory) {
      environment.EIDOS_DATA_DIR = options.dataDirectory;
    }

    this.child = spawn(options.pythonExecutable, ["-u", "-m", "eidos_runtime"], {
      cwd: options.runtimeRoot,
      env: environment,
      stdio: ["pipe", "pipe", "pipe"],
    });

    this.child.stdout.on("data", (chunk: Buffer) => this.handleStdoutChunk(chunk));

    const stderr = readline.createInterface({ input: this.child.stderr });
    stderr.on("line", (line) => options.onStderr?.(line));
    this.onNotification = options.onNotification;
    this.onApprovalRequest = options.onApprovalRequest;

    this.exitPromise = new Promise((resolve) => {
      this.child.once("error", (error) => this.failAll(error));
      this.child.once("close", (code) => {
        this.closed = true;
        this.failAll(new Error("Runtime process exited"));
        resolve(code ?? 1);
      });
    });
  }

  initialize(): Promise<InitializeResult> {
    return this.validatedRequest(
      "initialize",
      {
        client: { name: "eidos-desktop", version: "0.3.0" },
        protocolVersion: 1,
      },
      isInitializeResult,
    );
  }

  async createSession(
    workspaceRoot: string | null,
    options: {
      executionMode?: "local" | "worktree";
      baseRef?: string;
      includeLocalChanges?: boolean;
      operationId?: string;
    } = {},
  ): Promise<Session> {
    const { operationId = randomUUID(), ...executionOptions } = options;
    return this.validatedRequest(
      "session/create", { workspaceRoot, operationId, ...executionOptions }, isSession,
    );
  }

  createSessionBranch(
    sessionId: string,
    branch: string,
    operationId = randomUUID(),
  ): Promise<CreateBranchResult> {
    return this.validatedRequest(
      "session/createBranch",
      { sessionId, branch, operationId },
      isCreateBranchResult,
    );
  }

  handoffSession(
    sessionId: string,
    target: "local" | "worktree",
    operationId = randomUUID(),
  ): Promise<SessionHandoffResult> {
    return this.validatedRequest(
      "session/handoff",
      { sessionId, target, operationId },
      isSessionHandoff,
    );
  }

  restoreSessionWorktree(
    sessionId: string,
    operationId = randomUUID(),
  ): Promise<SessionRestoreWorktreeResult> {
    return this.validatedRequest(
      "session/restoreWorktree",
      { sessionId, operationId },
      isSessionRestoreWorktree,
    );
  }

  readWorktreeSettings(): Promise<WorktreeSettings> {
    return this.validatedRequest("settings/read", {}, isWorktreeSettings);
  }

  updateWorktreeSettings(input: {
    automaticCleanup: boolean;
    managedWorktreeLimit: number;
  }): Promise<WorktreeSettings> {
    return this.validatedRequest("settings/update", input, isWorktreeSettings);
  }

  readProjectGitContext(workspaceRoot: string): Promise<ProjectGitContext> {
    return this.validatedRequest(
      "project/gitContext", { workspaceRoot }, isProjectGitContext,
    );
  }

  listProjects(): Promise<ProjectListResult> {
    return this.validatedRequest("project/list", {}, isProjectListResult);
  }

  createProject(
    name: string | undefined,
    workspaceRoot: string,
    operationId = randomUUID(),
  ): Promise<Project> {
    return this.validatedRequest(
      "project/create",
      { name, workspaceRoot, operationId },
      isProject,
    );
  }

  deleteProject(
    projectId: string,
    operationId = randomUUID(),
  ): Promise<DeleteProjectResult> {
    return this.validatedRequest(
      "project/delete", { projectId, operationId }, isDeletedProjectResult,
    );
  }

  listSessions(
    options: { limit?: number; cursor?: string } = {},
  ): Promise<SessionListResult> {
    return this.validatedRequest("session/list", options, isSessionListResult);
  }

  readSession(
    sessionId: string,
    options: { itemLimit?: number; beforeItemId?: string } = {},
  ): Promise<SessionSnapshot> {
    return this.validatedRequest(
      "session/read",
      { sessionId, ...options },
      isSessionSnapshot,
    );
  }

  listWorkspaceDirectory(
    sessionId: string,
    path: string,
    limit = 500,
  ): Promise<WorkspaceDirectoryListing> {
    return this.validatedRequest(
      "workspace/listDirectory",
      { sessionId, path, limit },
      isWorkspaceDirectoryListing,
    );
  }

  readWorkspaceFilePreview(
    sessionId: string,
    path: string,
  ): Promise<WorkspaceFilePreview> {
    return this.validatedRequest(
      "workspace/readFilePreview",
      { sessionId, path },
      isWorkspaceFilePreview,
    );
  }

  renameSession(
    sessionId: string, title: string, operationId = randomUUID(),
  ): Promise<Session> {
    return this.validatedRequest(
      "session/rename", { sessionId, title, operationId }, isSession,
    );
  }

  deleteSession(
    sessionId: string, operationId = randomUUID(),
  ): Promise<{ deletedSessionId: string }> {
    return this.validatedRequest(
      "session/delete", { sessionId, operationId }, isDeletedSessionResult,
    );
  }

  readSessionGitStatus(sessionId: string): Promise<SessionGitStatus> {
    return this.validatedRequest(
      "session/gitStatus", { sessionId }, isSessionGitStatus,
    );
  }

  readSessionGitDiff(
    sessionId: string,
    scope: GitDiffScope,
    path?: string,
    compareRef?: string,
  ): Promise<SessionGitDiff> {
    return this.validatedRequest(
      "session/gitDiff",
      {
        sessionId,
        scope,
        ...(path === undefined ? {} : { path }),
        ...(compareRef === undefined ? {} : { compareRef }),
      },
      isSessionGitDiff,
    );
  }

  switchSessionGitBranch(
    sessionId: string,
    branch: string,
    operationId: string,
  ): Promise<SessionGitMutationResult> {
    return this.validatedRequest(
      "session/gitSwitchBranch", { operationId, sessionId, branch }, isSessionGitMutationResult,
    );
  }

  createSessionGitBranch(
    sessionId: string,
    branch: string,
    operationId: string,
  ): Promise<SessionGitMutationResult> {
    return this.validatedRequest(
      "session/gitCreateBranch", { operationId, sessionId, branch }, isSessionGitMutationResult,
    );
  }

  stageSessionGit(
    sessionId: string,
    paths: string[],
    operationId: string,
  ): Promise<SessionGitMutationResult> {
    return this.validatedRequest(
      "session/gitStage", { operationId, sessionId, paths }, isSessionGitMutationResult,
    );
  }

  unstageSessionGit(
    sessionId: string,
    paths: string[],
    operationId: string,
  ): Promise<SessionGitMutationResult> {
    return this.validatedRequest(
      "session/gitUnstage", { operationId, sessionId, paths }, isSessionGitMutationResult,
    );
  }

  commitSessionGit(
    sessionId: string,
    message: string,
    operationId: string,
  ): Promise<SessionGitCommitResult> {
    return this.validatedRequest(
      "session/gitCommit", { operationId, sessionId, message }, isSessionGitCommitResult,
    );
  }

  discardSessionGit(
    sessionId: string,
    path: string,
    operationId: string,
  ): Promise<SessionGitDiscardResult> {
    return this.validatedRequest(
      "session/gitDiscard", { operationId, sessionId, path }, isSessionGitMutationResult,
    );
  }

  listReviewComments(
    sessionId: string,
    path?: string,
    scope?: GitDiffScope,
  ): Promise<ReviewComment[]> {
    return this.validatedRequest(
      "review/listComments",
      { sessionId, ...(path === undefined ? {} : { path, scope }) },
      (value): value is { comments: ReviewComment[] } => (
        isRecord(value)
        && hasOnlyKeys(value, ["comments"])
        && Array.isArray(value.comments)
        && value.comments.every(isReviewComment)
      ),
    ).then((result) => result.comments);
  }

  createReviewComment(
    sessionId: string,
    input: ReviewCommentCreateInput,
    operationId: string,
  ): Promise<ReviewComment> {
    return this.validatedRequest(
      "review/createComment",
      { operationId, sessionId, ...input },
      (value): value is { comment: ReviewComment } => (
        isRecord(value)
        && hasOnlyKeys(value, ["comment"])
        && isReviewComment(value.comment)
      ),
    ).then((result) => result.comment);
  }

  deleteReviewComment(
    sessionId: string,
    commentId: string,
    operationId: string,
  ): Promise<string> {
    return this.validatedRequest(
      "review/deleteComment",
      { operationId, sessionId, commentId },
      (value): value is { commentId: string } => (
        isRecord(value)
        && hasOnlyKeys(value, ["commentId"])
        && typeof value.commentId === "string"
      ),
    ).then((result) => result.commentId);
  }

  readSessionGitRemoteStatus(sessionId: string): Promise<GitRemoteStatus> {
    return this.validatedRequest(
      "session/gitRemoteStatus", { sessionId }, isGitRemoteStatus,
    );
  }

  fetchSessionGit(
    sessionId: string,
    operationId: string,
    remote?: string,
  ): Promise<GitFetchResult> {
    return this.validatedRequest(
      "session/gitFetch",
      { operationId, sessionId, ...(remote === undefined ? {} : { remote }) },
      isGitFetchResult,
    );
  }

  pullSessionGit(
    sessionId: string,
    operationId: string,
  ): Promise<GitPullResult> {
    return this.validatedRequest(
      "session/gitPull", { operationId, sessionId }, isGitPullResult,
    );
  }

  pushSessionGit(
    sessionId: string,
    operationId: string,
    remote?: string,
  ): Promise<GitPushResult> {
    return this.validatedRequest(
      "session/gitPush",
      { operationId, sessionId, ...(remote === undefined ? {} : { remote }) },
      isGitPullResult,
    );
  }

  mergeSessionGit(
    sessionId: string,
    target: string,
    operationId: string,
  ): Promise<GitMergeResult> {
    return this.validatedRequest(
      "session/gitMerge",
      { operationId, sessionId, target },
      isGitMergeResult,
    );
  }

  abortSessionGitMerge(
    sessionId: string,
    operationId: string,
  ): Promise<GitMergeResult> {
    return this.validatedRequest(
      "session/gitMergeAbort",
      { operationId, sessionId },
      isGitMergeResult,
    );
  }

  rebaseSessionGit(
    sessionId: string,
    target: string,
    operationId: string,
  ): Promise<GitRebaseResult> {
    return this.validatedRequest(
      "session/gitRebase",
      { operationId, sessionId, target },
      isGitMergeResult,
    );
  }

  continueSessionGitRebase(
    sessionId: string,
    operationId: string,
  ): Promise<GitRebaseResult> {
    return this.validatedRequest(
      "session/gitRebaseContinue",
      { operationId, sessionId },
      isGitMergeResult,
    );
  }

  abortSessionGitRebase(
    sessionId: string,
    operationId: string,
  ): Promise<GitRebaseResult> {
    return this.validatedRequest(
      "session/gitRebaseAbort",
      { operationId, sessionId },
      isGitMergeResult,
    );
  }

  listEvents(sessionId: string, afterEventId: number, limit = 200): Promise<EventListResult> {
    return this.validatedRequest(
      "event/list", { sessionId, afterEventId, limit }, isEventListResult,
    );
  }

  health(): Promise<RuntimeHealth> {
    return this.validatedRequest("runtime/health", {}, isRuntimeHealth);
  }

  startRun(
    sessionId: string,
    userInput: string,
    modelId: ModelId,
    operationId = randomUUID(),
  ): Promise<Run> {
    return this.validatedRequest(
      "run/start", { sessionId, userInput, modelId, operationId }, isRun,
    );
  }

  cancelRun(runId: string, operationId = randomUUID()): Promise<Run> {
    return this.validatedRequest("run/cancel", { runId, operationId }, isRun);
  }

  async readContextUsage(runId: string): Promise<ContextUsage | null> {
    const result = await this.validatedRequest(
      "context/usage",
      { runId },
      isContextUsageResult,
    );
    const usage = result.contextUsage;
    return usage
      ? {
          activeTokens: usage.activeTokens,
          windowTokens: usage.contextWindowTokens,
          percentUsed: usage.percentUsed,
          source: usage.source,
          ...(usage.updatedAt !== undefined ? { updatedAt: usage.updatedAt } : {}),
        }
      : null;
  }

  listModelPresets(): Promise<ModelPresetsResult> {
    return this.validatedRequest("model/presets", {}, isModelPresetsResult);
  }

  listModels(): Promise<ModelListResult> {
    return this.validatedRequest("model/list", {}, isModelListResult);
  }

  createModel(input: ModelCreateInput): Promise<ModelOption> {
    return this.validatedRequest("model/create", { ...input }, isModelOption);
  }

  updateModel(input: ModelUpdateInput): Promise<ModelOption> {
    return this.validatedRequest("model/update", { ...input }, isModelOption);
  }

  deleteModel(id: ModelId): Promise<void> {
    return this.validatedRequest(
      "model/delete",
      { id },
      (value): value is { deletedModelId: string } => (
        isRecord(value)
        && hasOnlyKeys(value, ["deletedModelId"])
        && value.deletedModelId === id
      ),
    ).then(() => undefined);
  }

  listPlugins(): Promise<{ plugins: PluginRecord[] }> {
    return this.validatedRequest("plugin/list", {}, isPluginListResult);
  }

  importPlugin(sourcePath: string, operationId = randomUUID()): Promise<PluginRecord> {
    return this.validatedRequest(
      "plugin/import", { sourcePath, operationId }, isPluginRecord,
    );
  }

  setPluginEnabled(pluginId: string, enabled: boolean, operationId = randomUUID()): Promise<PluginRecord> {
    return this.validatedRequest(
      "plugin/setEnabled", { pluginId, enabled, operationId }, isPluginRecord,
    );
  }

  removePlugin(pluginId: string, operationId = randomUUID()): Promise<PluginRecord> {
    return this.validatedRequest(
      "plugin/remove", { pluginId, operationId }, isPluginRecord,
    );
  }

  listSkills(): Promise<{ skills: SkillMetadata[] }> {
    return this.validatedRequest("skill/list", {}, isSkillListResult);
  }

  listMcpServers(): Promise<{ servers: McpServerRecord[] }> {
    return this.validatedRequest("mcp/list", {}, isMcpServerListResult);
  }

  setMcpEnabled(
    pluginId: string, serverId: string, enabled: boolean,
    operationId = randomUUID(),
  ): Promise<McpServerRecord> {
    return this.validatedRequest(
      "mcp/setEnabled", { pluginId, serverId, enabled, consent: true, operationId },
      isMcpServerRecord,
    );
  }

  readExtensions(): Promise<ExtensionSnapshotResult> {
    return this.validatedRequest("extension/read", {}, isExtensionSnapshotResult);
  }

  readExtensionEvents(afterEventId: number, limit = 200): Promise<EventListResult> {
    return this.validatedRequest(
      "extension/readEvents", { afterEventId, limit }, isEventListResult,
    );
  }

  async shutdown(): Promise<void> {
    if (this.closed) {
      return;
    }
    await this.request("runtime/shutdown", {});
  }

  waitForExit(): Promise<number> {
    return this.exitPromise;
  }

  terminate(): void {
    if (!this.closed) {
      this.child.kill();
    }
  }

  private request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    if (this.closed || this.child.stdin.destroyed) {
      return Promise.reject(new Error("Runtime process is not available"));
    }

    const id = `client-${this.nextRequestId++}`;
    const serialized = JSON.stringify({ jsonrpc: "2.0", id, method, params });
    if (Buffer.byteLength(serialized, "utf8") > MAX_MESSAGE_BYTES) {
      return Promise.reject(new Error("Runtime request exceeds 1 MiB"));
    }

    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Runtime request timed out: ${method}`));
      }, 30_000);
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
        timeout,
      });
      this.child.stdin.write(`${serialized}\n`, "utf8", (error) => {
        if (!error) {
          return;
        }
        const pending = this.pending.get(id);
        if (pending) {
          clearTimeout(pending.timeout);
          this.pending.delete(id);
        }
        reject(error);
      });
    });
  }

  private async validatedRequest<T>(
    method: string,
    params: Record<string, unknown>,
    validate: (value: unknown) => value is T,
  ): Promise<T> {
    const result = await this.request<unknown>(method, params);
    if (!validate(result)) {
      this.failProtocol(`Runtime returned an invalid result for ${method}`);
      throw new Error(`Runtime returned an invalid result for ${method}`);
    }
    return result;
  }

  private handleStdoutChunk(chunk: Buffer): void {
    if (this.closed) {
      return;
    }
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);
    let newline = this.stdoutBuffer.indexOf(0x0a);
    while (newline >= 0) {
      if (newline > MAX_MESSAGE_BYTES) {
        this.failProtocol("Runtime response exceeds 1 MiB");
        return;
      }
      let line = this.stdoutBuffer.subarray(0, newline);
      this.stdoutBuffer = this.stdoutBuffer.subarray(newline + 1);
      if (line.length > 0 && line[line.length - 1] === 0x0d) {
        line = line.subarray(0, line.length - 1);
      }
      this.handleLine(line.toString("utf8"));
      if (this.closed) {
        return;
      }
      newline = this.stdoutBuffer.indexOf(0x0a);
    }
    if (this.stdoutBuffer.length > MAX_MESSAGE_BYTES) {
      this.failProtocol("Runtime response exceeds 1 MiB");
    }
  }

  private handleLine(line: string): void {
    if (Buffer.byteLength(line, "utf8") > MAX_MESSAGE_BYTES) {
      this.failProtocol("Runtime response exceeds 1 MiB");
      return;
    }

    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      this.failProtocol("Runtime wrote invalid JSON to stdout");
      return;
    }

    if (isNotification(message)) {
      this.onNotification?.(message);
      return;
    }
    if (isServerRequest(message)) {
      const request = approvalRequestFrom(message);
      if (!request || !this.onApprovalRequest) {
        this.writeProtocolMessage({
          jsonrpc: "2.0",
          id: message.id,
          error: { code: -32601, message: "Method not found" },
        });
        return;
      }
      void this.onApprovalRequest(request).then((decision) => {
        this.writeProtocolMessage({ jsonrpc: "2.0", id: message.id, result: decision });
      }).catch(() => {
        this.writeProtocolMessage({
          jsonrpc: "2.0",
          id: message.id,
          error: { code: -32000, message: "Approval failed" },
        });
      });
      return;
    }
    if (!isResponse(message)) {
      this.failProtocol("Runtime wrote an invalid JSON-RPC message");
      return;
    }

    const pending = this.pending.get(message.id);
    if (!pending) {
      this.failProtocol("Runtime returned an unknown response id");
      return;
    }
    this.pending.delete(message.id);
    clearTimeout(pending.timeout);

    if ("error" in message) {
      pending.reject(new RuntimeRequestError(message.error));
      return;
    }
    pending.resolve(message.result);
  }

  private writeProtocolMessage(message: Record<string, unknown>): void {
    if (this.closed || this.child.stdin.destroyed) {
      return;
    }
    const serialized = JSON.stringify(message);
    if (Buffer.byteLength(serialized, "utf8") > MAX_MESSAGE_BYTES) {
      this.failProtocol("Runtime protocol response exceeds 1 MiB");
      return;
    }
    this.child.stdin.write(`${serialized}\n`, "utf8");
  }

  private failProtocol(message: string): void {
    this.closed = true;
    this.failAll(new Error(message));
    this.child.kill();
  }

  private failAll(error: Error): void {
    for (const request of this.pending.values()) {
      clearTimeout(request.timeout);
      request.reject(error);
    }
    this.pending.clear();
  }
}

function isResponse(
  value: unknown,
): value is
  | { jsonrpc: "2.0"; id: string; result: unknown }
  | { jsonrpc: "2.0"; id: string; error: RpcError } {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  if (candidate.jsonrpc !== "2.0" || typeof candidate.id !== "string") {
    return false;
  }
  if (("result" in candidate) === ("error" in candidate)) {
    return false;
  }
  if ("result" in candidate) {
    return true;
  }
  const error = candidate.error;
  return (
    Boolean(error)
    && typeof error === "object"
    && typeof (error as Record<string, unknown>).code === "number"
    && typeof (error as Record<string, unknown>).message === "string"
  );
}

function isServerRequest(
  value: unknown,
): value is { jsonrpc: "2.0"; id: string; method: string; params: unknown } {
  if (!isRecord(value)) {
    return false;
  }
  return (
    value.jsonrpc === "2.0"
    && typeof value.id === "string"
    && value.id.startsWith("server-")
    && typeof value.method === "string"
    && "params" in value
    && hasOnlyKeys(value, ["jsonrpc", "id", "method", "params"])
  );
}

function isNotification(value: unknown): value is RuntimeNotification {
  if (
    !isRecord(value)
    || value.jsonrpc !== "2.0"
    || "id" in value
    || typeof value.method !== "string"
    || !isRecord(value.params)
    || !hasOnlyKeys(value, ["jsonrpc", "method", "params"])
  ) {
    return false;
  }
  const params = value.params;
  if (value.method === "workspace/changed") {
    return (
      hasOnlyKeys(params, ["sessionId", "paths"])
      && typeof params.sessionId === "string"
      && isStringArray(params.paths)
    );
  }
  if (value.method === "session/titleUpdated") {
    return (
      hasOnlyKeys(params, ["sessionId", "title"])
      && typeof params.sessionId === "string"
      && typeof params.title === "string"
      && params.title.length > 0
    );
  }
  if (
    value.method === "run/started"
    || value.method === "run/updated"
    || value.method === "run/completed"
  ) {
    const run = params.run;
    const valid = (
      hasOnlyKeys(params, ["sessionId", "run"])
      && typeof params.sessionId === "string"
      && isRun(run)
      && params.sessionId === run.sessionId
    );
    if (!valid || !isRun(run)) {
      return false;
    }
    if (value.method === "run/started") {
      return run.status === "running";
    }
    if (value.method === "run/updated") {
      return ["queued", "running", "waiting_approval", "finalizing"].includes(run.status);
    }
    return ![
      "queued", "running", "waiting_approval", "finalizing",
    ].includes(run.status);
  }
  if (value.method === "item/started" || value.method === "item/completed") {
    const item = params.item;
    const valid = (
      hasOnlyKeys(params, ["sessionId", "runId", "item"])
      && typeof params.sessionId === "string"
      && typeof params.runId === "string"
      && isItem(item)
      && params.sessionId === item.sessionId
      && params.runId === item.runId
    );
    if (!valid || !isItem(item)) {
      return false;
    }
    return value.method === "item/started"
      ? item.status === "in_progress" && item.completedAt === undefined
      : item.status !== "in_progress" && item.completedAt !== undefined;
  }
  if (value.method === "item/delta") {
    return (
      hasOnlyKeys(params, ["sessionId", "runId", "itemId", "sequence", "delta"])
      && typeof params.sessionId === "string"
      && typeof params.runId === "string"
      && typeof params.itemId === "string"
      && isPositiveInteger(params.sequence)
      && typeof params.delta === "string"
    );
  }
  if (
    value.method === "approval/requested"
    || value.method === "approval/resolved"
    || value.method === "approval/canceled"
  ) {
    const status = String(params.status);
    return (
      hasOnlyKeys(params, ["sessionId", "runId", "approvalId", "status"])
      && typeof params.sessionId === "string"
      && typeof params.runId === "string"
      && typeof params.approvalId === "string"
      && (
        (value.method === "approval/requested" && status === "pending")
        || (
          value.method === "approval/resolved"
          && ["approved", "rejected"].includes(status)
        )
        || (
          value.method === "approval/canceled"
          && ["canceled", "invalidated"].includes(status)
        )
      )
    );
  }
  return false;
}

function isInitializeResult(value: unknown): value is InitializeResult {
  if (!isRecord(value) || !hasOnlyKeys(value, ["protocolVersion", "runtimeVersion", "capabilities"])) {
    return false;
  }
  return (
    isNonNegativeInteger(value.protocolVersion)
    && typeof value.runtimeVersion === "string"
    && isRecord(value.capabilities)
    && hasOnlyKeys(value.capabilities, ["runShell", "modelConfigured"])
    && typeof value.capabilities.runShell === "boolean"
    && typeof value.capabilities.modelConfigured === "boolean"
  );
}

function isModelOption(value: unknown): value is ModelOption {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "id", "name", "vendor", "provider", "url", "supportsToolCall",
      "supportsImages", "supportsReasoning", "reasoning",
    ])
    && isModelId(value.id)
    && typeof value.name === "string"
    && typeof value.vendor === "string"
    && typeof value.provider === "string"
    && typeof value.url === "string"
    && typeof value.supportsToolCall === "boolean"
    && typeof value.supportsImages === "boolean"
    && typeof value.supportsReasoning === "boolean"
    && (
      value.reasoning === undefined
      || value.reasoning === null
      || isModelReasoning(value.reasoning)
    )
  );
}

function isModelReasoning(value: unknown): boolean {
  return isRecord(value)
    && hasOnlyKeys(value, ["defaultEffort", "supportedEfforts"])
    && ["high", "max"].includes(String(value.defaultEffort))
    && Array.isArray(value.supportedEfforts)
    && value.supportedEfforts.every((effort) => ["high", "max"].includes(String(effort)));
}

function isModelId(value: unknown): value is ModelId {
  return typeof value === "string" && value.length > 0 && value.length <= 256;
}

function isModelListResult(value: unknown): value is ModelListResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["models", "defaultModelId"])
    && (
      value.defaultModelId === undefined
      || value.defaultModelId === null
      || isModelId(value.defaultModelId)
    )
    && Array.isArray(value.models)
    && value.models.every(isModelOption)
  );
}

function isModelPresetsResult(value: unknown): value is ModelPresetsResult {
  return isRecord(value)
    && hasOnlyKeys(value, ["providers"])
    && Array.isArray(value.providers)
    && value.providers.every((provider) => (
      isRecord(provider)
      && hasOnlyKeys(provider, ["id", "name", "models"])
      && ["deepseek", "minimax", "kimi", "volcengine"].includes(String(provider.id))
      && typeof provider.name === "string"
      && Array.isArray(provider.models)
      && provider.models.every((model) => (
        isRecord(model)
        && isModelOption({ ...model, vendor: provider.name, provider: provider.id })
      ))
    ));
}

function isRuntimeHealth(value: unknown): value is RuntimeHealth {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["state", "code"])
    && ["ready", "health_only"].includes(String(value.state))
    && (value.code === undefined || typeof value.code === "string")
  );
}

function isSession(value: unknown): value is Session {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "id", "workspaceRoot", "executionMode", "associatedWorktreeId",
      "worktreeRestoreAvailable", "projectless", "project", "worktree", "title", "taskStatus",
      "createdAt", "updatedAt",
    ])
    && typeof value.id === "string"
    && typeof value.workspaceRoot === "string"
    && (value.projectless === undefined || typeof value.projectless === "boolean")
    && (value.executionMode === undefined || ["local", "worktree"].includes(String(value.executionMode)))
    && (value.associatedWorktreeId === undefined || typeof value.associatedWorktreeId === "string")
    && (value.worktreeRestoreAvailable === undefined || typeof value.worktreeRestoreAvailable === "boolean")
    && (value.project === undefined || isSessionProject(value.project))
    && (value.worktree === undefined || isSessionWorktree(value.worktree))
    && (value.title === undefined || typeof value.title === "string")
    && ["new", "in_progress", "completed", "failed", "canceled"].includes(String(value.taskStatus))
    && isNonNegativeInteger(value.createdAt)
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isSessionHandoff(value: unknown): value is SessionHandoffResult {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id", "sessionId", "worktreeId", "workspaceRoot", "executionMode",
      "associatedWorktreeId", "projectless", "project", "worktree", "title", "taskStatus",
      "worktreeRestoreAvailable", "createdAt", "updatedAt",
    ])
    || typeof value.sessionId !== "string"
    || (value.worktreeId !== null && typeof value.worktreeId !== "string")
  ) return false;
  const session = { ...value };
  delete session.sessionId;
  delete session.worktreeId;
  return isSession(session);
}

function isSessionRestoreWorktree(value: unknown): value is SessionRestoreWorktreeResult {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id", "sessionId", "worktreeId", "workspaceRoot", "executionMode",
      "associatedWorktreeId", "worktreeRestoreAvailable", "projectless", "project", "worktree",
      "title", "taskStatus", "createdAt", "updatedAt",
    ])
    || typeof value.sessionId !== "string"
    || typeof value.worktreeId !== "string"
  ) return false;
  const session = { ...value };
  delete session.sessionId;
  delete session.worktreeId;
  return isSession(session);
}

function isWorktreeSettings(value: unknown): value is WorktreeSettings {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["automaticCleanup", "managedWorktreeLimit", "updatedAt"])
    && typeof value.automaticCleanup === "boolean"
    && Number.isInteger(value.managedWorktreeLimit)
    && Number(value.managedWorktreeLimit) >= 1
    && Number(value.managedWorktreeLimit) <= 100
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isSessionProject(value: unknown): boolean {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["id", "name", "workspaceRoot", "gitAvailable"])
    && typeof value.id === "string"
    && (value.name === undefined || typeof value.name === "string")
    && typeof value.workspaceRoot === "string"
    && typeof value.gitAvailable === "boolean"
  );
}

function isProject(value: unknown): value is ProjectListResult["items"][number] {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["id", "name", "workspaceRoot", "gitAvailable", "createdAt", "updatedAt"])
    && typeof value.id === "string"
    && (value.name === undefined || typeof value.name === "string")
    && typeof value.workspaceRoot === "string"
    && typeof value.gitAvailable === "boolean"
    && isNonNegativeInteger(value.createdAt)
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isProjectListResult(value: unknown): value is ProjectListResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["items"])
    && Array.isArray(value.items)
    && value.items.every(isProject)
  );
}

function isSessionWorktree(value: unknown): boolean {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "worktreeId", "projectId", "repositoryRoot", "worktreeRoot",
      "baseRef", "baseCommit", "branch", "state",
    ])
    && typeof value.worktreeId === "string"
    && typeof value.projectId === "string"
    && typeof value.repositoryRoot === "string"
    && typeof value.worktreeRoot === "string"
    && typeof value.baseRef === "string"
    && typeof value.baseCommit === "string"
    && (value.branch === null || typeof value.branch === "string")
    && ["active", "missing", "invalid", "deleted"].includes(String(value.state))
  );
}

function isSessionGitStatus(value: unknown): value is SessionGitStatus {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "worktreeId", "branch", "head", "baseRef", "baseCommit", "dirty",
      "stagedCount", "unstagedCount", "untrackedCount", "conflictCount", "observedAt",
      "stagedFiles", "unstagedFiles", "untrackedFiles", "conflictFiles",
    ])
    && (value.worktreeId === null || typeof value.worktreeId === "string")
    && (value.branch === null || typeof value.branch === "string")
    && typeof value.head === "string"
    && (value.baseRef === null || typeof value.baseRef === "string")
    && (value.baseCommit === null || typeof value.baseCommit === "string")
    && typeof value.dirty === "boolean"
    && isNonNegativeInteger(value.stagedCount)
    && isNonNegativeInteger(value.unstagedCount)
    && isNonNegativeInteger(value.untrackedCount)
    && isNonNegativeInteger(value.conflictCount)
    && isStringArray(value.stagedFiles)
    && isStringArray(value.unstagedFiles)
    && isStringArray(value.untrackedFiles)
    && isStringArray(value.conflictFiles)
    && isNonNegativeInteger(value.observedAt)
  );
}

function isWorkspaceDirectoryListing(value: unknown): value is WorkspaceDirectoryListing {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["path", "entries", "truncated"])
    && typeof value.path === "string"
    && typeof value.truncated === "boolean"
    && Array.isArray(value.entries)
    && value.entries.every((entry) => (
      isRecord(entry)
      && hasOnlyKeys(entry, ["name", "relativePath", "kind", "sizeBytes"])
      && typeof entry.name === "string"
      && typeof entry.relativePath === "string"
      && ["file", "directory"].includes(String(entry.kind))
      && (entry.sizeBytes === undefined || isNonNegativeInteger(entry.sizeBytes))
    ))
  );
}

function isWorkspaceFilePreview(value: unknown): value is WorkspaceFilePreview {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "path", "kind", "sizeBytes", "truncated", "content", "language", "reason",
    ])
    && typeof value.path === "string"
    && ["text", "markdown", "code", "unavailable"].includes(String(value.kind))
    && isNonNegativeInteger(value.sizeBytes)
    && typeof value.truncated === "boolean"
    && (value.content === undefined || typeof value.content === "string")
    && (value.language === undefined || typeof value.language === "string")
    && (value.reason === undefined || ["binary", "unsupported"].includes(String(value.reason)))
  );
}

function isProjectGitContext(value: unknown): value is ProjectGitContext {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "gitAvailable", "currentBranch", "head", "branches", "dirty", "changedFileCount",
    ])
    && typeof value.gitAvailable === "boolean"
    && (value.currentBranch === null || typeof value.currentBranch === "string")
    && (value.head === null || typeof value.head === "string")
    && Array.isArray(value.branches)
    && value.branches.every((branch) => typeof branch === "string")
    && typeof value.dirty === "boolean"
    && isNonNegativeInteger(value.changedFileCount)
  );
}

function isCreateBranchResult(value: unknown): value is CreateBranchResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["sessionId", "worktreeId", "branch", "head"])
    && typeof value.sessionId === "string"
    && typeof value.worktreeId === "string"
    && typeof value.branch === "string"
    && typeof value.head === "string"
  );
}

function isSessionGitDiff(value: unknown): value is SessionGitDiff {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "scope", "compareRef", "baseCommit", "head", "dirty", "changedFiles",
      "unifiedDiff", "diffHash", "truncated", "additions", "deletions",
      "statsIncomplete", "fileStats", "observedAt",
    ])
    && ["head", "baseline"].includes(String(value.scope))
    && (value.compareRef === null || typeof value.compareRef === "string")
    && (value.baseCommit === null || typeof value.baseCommit === "string")
    && typeof value.head === "string"
    && typeof value.dirty === "boolean"
    && Array.isArray(value.changedFiles)
    && value.changedFiles.every((path) => typeof path === "string")
    && typeof value.unifiedDiff === "string"
    && typeof value.diffHash === "string"
    && typeof value.truncated === "boolean"
    && isNonNegativeInteger(value.additions)
    && isNonNegativeInteger(value.deletions)
    && typeof value.statsIncomplete === "boolean"
    && Array.isArray(value.fileStats)
    && value.fileStats.every(isSessionGitFileStat)
    && isNonNegativeInteger(value.observedAt)
  );
}

function isSessionGitFileStat(value: unknown): boolean {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["path", "additions", "deletions", "statsIncomplete"])
    && typeof value.path === "string"
    && isNonNegativeInteger(value.additions)
    && isNonNegativeInteger(value.deletions)
    && typeof value.statsIncomplete === "boolean"
  );
}

function isReviewComment(value: unknown): value is ReviewComment {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "id", "sessionId", "path", "scope", "side", "line", "body",
      "baseHead", "diffHash", "status", "createdAt", "updatedAt",
    ])
    && typeof value.id === "string"
    && typeof value.sessionId === "string"
    && typeof value.path === "string"
    && ["head", "baseline"].includes(String(value.scope))
    && ["old", "new"].includes(String(value.side))
    && isNonNegativeInteger(value.line)
    && value.line > 0
    && typeof value.body === "string"
    && typeof value.baseHead === "string"
    && typeof value.diffHash === "string"
    && ["active", "stale"].includes(String(value.status))
    && isNonNegativeInteger(value.createdAt)
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isSessionGitMutationResult(value: unknown): value is SessionGitMutationResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["head", "branch", "status"])
    && typeof value.head === "string"
    && (value.branch === null || typeof value.branch === "string")
    && isSessionGitStatus(value.status)
  );
}

function isSessionGitCommitResult(value: unknown): value is SessionGitCommitResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["head", "branch", "status", "commit"])
    && typeof value.head === "string"
    && (value.branch === null || typeof value.branch === "string")
    && isSessionGitStatus(value.status)
    && typeof value.commit === "string"
    && value.commit === value.head
  );
}

function isGitRemoteStatus(value: unknown): value is GitRemoteStatus {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["branch", "remotes", "upstream", "ahead", "behind"])
    && (value.branch === null || typeof value.branch === "string")
    && Array.isArray(value.remotes)
    && value.remotes.every((remote) => (
      isRecord(remote)
      && hasOnlyKeys(remote, ["name"])
      && typeof remote.name === "string"
    ))
    && (
      value.upstream === null
      || (
        isRecord(value.upstream)
        && hasOnlyKeys(value.upstream, ["remote", "branch"])
        && typeof value.upstream.remote === "string"
        && typeof value.upstream.branch === "string"
      )
    )
    && (value.ahead === null || isNonNegativeInteger(value.ahead))
    && (value.behind === null || isNonNegativeInteger(value.behind))
  );
}

function isGitFetchResult(value: unknown): value is GitFetchResult {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    "branch", "remotes", "upstream", "ahead", "behind", "remote", "head",
  ])) return false;
  const { remote, head, ...status } = value;
  return typeof remote === "string" && typeof head === "string" && isGitRemoteStatus(status);
}

function isGitPullResult(value: unknown): value is GitPullResult {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    "branch", "remotes", "upstream", "ahead", "behind", "remote", "head", "status",
  ])) return false;
  const { status, ...fetch } = value;
  return isGitFetchResult(fetch) && isSessionGitStatus(status) && status.head === value.head;
}

function isGitMergeResult(value: unknown): value is GitMergeResult {
  if (!isRecord(value) || !hasOnlyKeys(value, [
    "head", "branch", "status", "operationState", "conflictFiles",
  ])) return false;
  const { operationState, conflictFiles, ...mutation } = value;
  return (
    isSessionGitMutationResult(mutation)
    && ["none", "merge", "rebase"].includes(String(operationState))
    && Array.isArray(conflictFiles)
    && conflictFiles.every((path) => typeof path === "string")
  );
}

function isSessionListResult(value: unknown): value is SessionListResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["items", "nextCursor"])
    && Array.isArray(value.items)
    && value.items.every(isSession)
    && (value.nextCursor === undefined || typeof value.nextCursor === "string")
  );
}

function isSessionSnapshot(value: unknown): value is SessionSnapshot {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "session", "runs", "items", "stepResolutions",
      "previousItemId", "throughEventId",
    ])
    && isSession(value.session)
    && Array.isArray(value.runs)
    && value.runs.every(isRun)
    && Array.isArray(value.items)
    && value.items.every(isItem)
    && Array.isArray(value.stepResolutions)
    && value.stepResolutions.every(isStepResolutionReview)
    && (value.previousItemId === undefined || typeof value.previousItemId === "string")
    && (value.throughEventId === undefined || isNonNegativeInteger(value.throughEventId))
  );
}

function isStepResolutionReview(value: unknown): boolean {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "id", "stepId", "runId", "stepOrdinal", "snapshotHash", "requestHash",
      "ruleSnapshotId", "ruleSnapshotHash", "rules", "shadowed", "warnings",
    ])
    && typeof value.id === "string"
    && typeof value.stepId === "string"
    && typeof value.runId === "string"
    && isPositiveInteger(value.stepOrdinal)
    && isSha256(value.snapshotHash)
    && isSha256(value.requestHash)
    && typeof value.ruleSnapshotId === "string"
    && isSha256(value.ruleSnapshotHash)
    && Array.isArray(value.rules)
    && value.rules.every((rule) => (
      isRecord(rule)
      && hasOnlyKeys(rule, [
        "absolutePath", "relativePath", "filename", "contentHash", "byteCount",
        "includedByteCount", "directoryLevel", "selectionReason", "truncated",
      ])
      && typeof rule.absolutePath === "string"
      && typeof rule.relativePath === "string"
      && typeof rule.filename === "string"
      && isSha256(rule.contentHash)
      && isNonNegativeInteger(rule.byteCount)
      && isNonNegativeInteger(rule.includedByteCount)
      && isNonNegativeInteger(rule.directoryLevel)
      && [
        "eidos_override", "eidos_native", "compatibility_fallback",
      ].includes(String(rule.selectionReason))
      && typeof rule.truncated === "boolean"
    ))
    && Array.isArray(value.shadowed)
    && value.shadowed.every((candidate) => (
      isRecord(candidate)
      && hasOnlyKeys(candidate, [
        "absolutePath", "relativePath", "filename", "directoryLevel", "reason",
      ])
      && typeof candidate.absolutePath === "string"
      && typeof candidate.relativePath === "string"
      && typeof candidate.filename === "string"
      && isNonNegativeInteger(candidate.directoryLevel)
      && candidate.reason === "higher_precedence_candidate_selected"
    ))
    && Array.isArray(value.warnings)
    && value.warnings.every((warning) => (
      isRecord(warning)
      && hasOnlyKeys(warning, ["code", "path", "message"])
      && [
        "RULE_BUDGET_TRUNCATED", "RULE_READ_ERROR",
        "RULE_PATH_OUTSIDE_WORKSPACE",
      ].includes(String(warning.code))
      && typeof warning.path === "string"
      && typeof warning.message === "string"
    ))
  );
}

function isEventListResult(value: unknown): value is EventListResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["items", "hasMore", "throughEventId"])
    && Array.isArray(value.items)
    && value.items.every((event) => (
      isRecord(event)
      && hasOnlyKeys(event, [
        "eventContractVersion", "eventId", "eventType", "occurredAt",
        "sessionId", "runId", "payload",
      ])
      && event.eventContractVersion === 1
      && isPositiveInteger(event.eventId)
      && typeof event.eventType === "string"
      && isNonNegativeInteger(event.occurredAt)
      && (event.sessionId === undefined || typeof event.sessionId === "string")
      && (event.runId === undefined || typeof event.runId === "string")
      && isRecord(event.payload)
    ))
    && typeof value.hasMore === "boolean"
    && isNonNegativeInteger(value.throughEventId)
  );
}

function isRun(value: unknown): value is Run {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id",
      "sessionId",
      "userInput",
      "status",
      "runtimeState",
      "modelId",
      "modelStepCount",
      "allowedActions",
      "createdAt",
      "startedAt",
      "updatedAt",
      "completedAt",
      "errorCode",
      "cancelRequestedAt",
      "cancelCompletedAt",
      "cancelFailureCode",
      "stopReason",
      "sideEffectsMayExist",
      "extensionSnapshot",
      "activatedTools",
    ])
  ) {
    return false;
  }
  return (
    typeof value.id === "string"
    && typeof value.sessionId === "string"
    && (value.userInput === undefined || typeof value.userInput === "string")
    && [
      "queued", "running", "waiting_approval",
      "finalizing", "stopped", "succeeded", "failed", "canceled", "interrupted",
    ].includes(String(value.status))
    && (
      value.runtimeState === undefined
      || [
        "queued", "thinking", "tool_executing",
        "waiting_approval", "finalizing", "terminal",
      ].includes(String(value.runtimeState))
    )
    && isModelId(value.modelId)
    && isNonNegativeInteger(value.modelStepCount)
    && (value.allowedActions === undefined || (
      Array.isArray(value.allowedActions)
      && value.allowedActions.every((action) => [
        "cancel", "approve", "reject",
      ].includes(String(action)))
    ))
    && isNonNegativeInteger(value.createdAt)
    && (value.startedAt === undefined || isNonNegativeInteger(value.startedAt))
    && isNonNegativeInteger(value.updatedAt)
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
    && (value.errorCode === undefined || typeof value.errorCode === "string")
    && (
      value.cancelRequestedAt === undefined
      || isNonNegativeInteger(value.cancelRequestedAt)
    )
    && (
      value.cancelCompletedAt === undefined
      || isNonNegativeInteger(value.cancelCompletedAt)
    )
    && (
      value.cancelFailureCode === undefined
      || typeof value.cancelFailureCode === "string"
    )
    && (value.stopReason === undefined || typeof value.stopReason === "string")
    && (value.sideEffectsMayExist === undefined || typeof value.sideEffectsMayExist === "boolean")
    && (value.extensionSnapshot === undefined || isExtensionSnapshot(value.extensionSnapshot))
    && (value.activatedTools === undefined || (
      Array.isArray(value.activatedTools)
      && value.activatedTools.every((name) => typeof name === "string")
    ))
  );
}

type RuntimeContextUsage = Omit<ContextUsage, "windowTokens"> & {
  contextWindowTokens: number;
};

function isContextUsage(value: unknown): value is RuntimeContextUsage {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "activeTokens", "contextWindowTokens", "percentUsed", "source", "updatedAt",
    ])
    && isNonNegativeInteger(value.activeTokens)
    && isPositiveInteger(value.contextWindowTokens)
    && typeof value.percentUsed === "number"
    && Number.isFinite(value.percentUsed)
    && value.percentUsed >= 0
    && value.percentUsed <= 100
    && ["provider", "estimated"].includes(String(value.source))
    && (value.updatedAt === undefined || isNonNegativeInteger(value.updatedAt))
  );
}

function isContextUsageResult(
  value: unknown,
): value is { contextUsage?: RuntimeContextUsage } {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["contextUsage"])
    && (value.contextUsage === undefined || isContextUsage(value.contextUsage))
  );
}

function isDeletedSessionResult(value: unknown): value is { deletedSessionId: string } {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["deletedSessionId"])
    && typeof value.deletedSessionId === "string"
  );
}

function isDeletedProjectResult(value: unknown): value is DeleteProjectResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["deletedProjectId"])
    && typeof value.deletedProjectId === "string"
  );
}

function isItem(value: unknown): value is Item {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id",
      "sessionId",
      "runId",
      "ordinal",
      "modelStepIndex",
      "kind",
      "status",
      "content",
      "incomplete",
      "createdAt",
      "completedAt",
      "toolCall",
    ])
  ) {
    return false;
  }
  const valid = (
    typeof value.id === "string"
    && typeof value.sessionId === "string"
    && typeof value.runId === "string"
    && isNonNegativeInteger(value.ordinal)
    && ["user_message", "assistant_message", "file_change", "command_execution", "tool_call"].includes(String(value.kind))
    && ["in_progress", "completed", "failed", "declined", "canceled"].includes(String(value.status))
    && isNonNegativeInteger(value.createdAt)
    && (value.modelStepIndex === undefined || isNonNegativeInteger(value.modelStepIndex))
    && (value.content === undefined || typeof value.content === "string")
    && (value.incomplete === undefined || typeof value.incomplete === "boolean")
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
  );
  if (!valid) {
    return false;
  }
  return ["tool_call", "file_change", "command_execution"].includes(String(value.kind))
    ? isToolCall(value.toolCall)
    : value.toolCall === undefined;
}

function isToolCall(value: unknown): value is ToolCall {
  if (
    !isRecord(value)
    || !hasOnlyKeys(value, [
      "id",
      "itemId",
      "modelStepIndex",
      "batchOrder",
      "providerCallId",
      "toolName",
      "status",
      "argumentsJson",
      "resultJson",
      "startedAt",
      "completedAt",
      "approvalStatus",
      "approvalDecision",
      "approvalFeedback",
      "changeDiff",
      "baseSha256",
      "provenance",
      "toolSetHash",
    ])
  ) {
    return false;
  }
  return (
    typeof value.id === "string"
    && typeof value.itemId === "string"
    && isNonNegativeInteger(value.modelStepIndex)
    && isNonNegativeInteger(value.batchOrder)
    && typeof value.providerCallId === "string"
    && typeof value.toolName === "string"
    && ["running", "completed", "failed", "canceled"].includes(String(value.status))
    && (value.argumentsJson === undefined || typeof value.argumentsJson === "string")
    && (value.resultJson === undefined || typeof value.resultJson === "string")
    && isNonNegativeInteger(value.startedAt)
    && (value.completedAt === undefined || isNonNegativeInteger(value.completedAt))
    && (value.approvalStatus === undefined || ["pending", "resolved", "canceled"].includes(String(value.approvalStatus)))
    && (value.approvalDecision === undefined || ["approve", "reject"].includes(String(value.approvalDecision)))
    && (value.approvalFeedback === undefined || typeof value.approvalFeedback === "string")
    && (value.changeDiff === undefined || typeof value.changeDiff === "string")
    && (value.baseSha256 === undefined || typeof value.baseSha256 === "string")
    && (value.provenance === undefined || isToolProvenance(value.provenance))
    && (value.toolSetHash === undefined || typeof value.toolSetHash === "string")
  );
}

function isToolProvenance(value: unknown): value is ToolProvenance {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "kind", "sourceId", "sourceVersion", "contentHash",
      "pluginId", "serverId", "skillId",
    ])
    && ["builtin", "skill", "mcp"].includes(String(value.kind))
    && typeof value.sourceId === "string"
    && typeof value.sourceVersion === "string"
    && typeof value.contentHash === "string"
    && (value.pluginId === undefined || typeof value.pluginId === "string")
    && (value.serverId === undefined || typeof value.serverId === "string")
    && (value.skillId === undefined || typeof value.skillId === "string")
  );
}

function projectApprovalToolProvenance(
  value: unknown,
): ToolProvenance | undefined {
  if (
    !isRecord(value)
    || !["builtin", "skill", "mcp"].includes(String(value.kind))
    || typeof value.sourceId !== "string"
    || typeof value.sourceVersion !== "string"
    || typeof value.contentHash !== "string"
    || (value.pluginId !== undefined && typeof value.pluginId !== "string")
    || (value.serverId !== undefined && typeof value.serverId !== "string")
    || (value.skillId !== undefined && typeof value.skillId !== "string")
  ) {
    return undefined;
  }
  return {
    kind: value.kind as "builtin" | "skill" | "mcp",
    sourceId: value.sourceId,
    sourceVersion: value.sourceVersion,
    contentHash: value.contentHash,
    ...(value.pluginId !== undefined ? { pluginId: value.pluginId } : {}),
    ...(value.serverId !== undefined ? { serverId: value.serverId } : {}),
    ...(value.skillId !== undefined ? { skillId: value.skillId } : {}),
  };
}

function isExtensionSnapshot(value: unknown): value is Record<string, unknown> {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "extensionContractVersion", "plugins",
      "skillCatalogHash", "mcpConfigHash",
    ])
    && value.schemaVersion === 1
    && value.extensionContractVersion === 1
    && typeof value.skillCatalogHash === "string"
    && typeof value.mcpConfigHash === "string"
    && Array.isArray(value.plugins)
    && value.plugins.every((plugin) => (
      isRecord(plugin)
      && hasOnlyKeys(plugin, ["id", "version", "contentHash"])
      && typeof plugin.id === "string"
      && typeof plugin.version === "string"
      && typeof plugin.contentHash === "string"
    ))
  );
}

function isPluginRecord(value: unknown): value is PluginRecord {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "id", "name", "version", "description", "contentHash",
      "enabled", "status", "installedAt", "updatedAt",
    ])
    && value.schemaVersion === 1
    && ["id", "name", "version", "description", "contentHash"].every(
      (key) => typeof value[key] === "string",
    )
    && typeof value.enabled === "boolean"
    && ["installed", "removed"].includes(String(value.status))
    && isNonNegativeInteger(value.installedAt)
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isPluginListResult(value: unknown): value is { plugins: PluginRecord[] } {
  return isRecord(value) && hasOnlyKeys(value, ["plugins"])
    && Array.isArray(value.plugins) && value.plugins.every(isPluginRecord);
}

function isSkillMetadata(value: unknown): value is SkillMetadata {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "qualifiedId", "name", "description", "pluginId",
      "pluginVersion", "pluginHash", "contentHash",
    ])
    && value.schemaVersion === 1
    && [
      "qualifiedId", "name", "description", "pluginId",
      "pluginVersion", "pluginHash", "contentHash",
    ].every((key) => typeof value[key] === "string")
  );
}

function isSkillListResult(value: unknown): value is { skills: SkillMetadata[] } {
  return isRecord(value) && hasOnlyKeys(value, ["skills"])
    && Array.isArray(value.skills) && value.skills.every(isSkillMetadata);
}

function isMcpServerRecord(value: unknown): value is McpServerRecord {
  return (
    isRecord(value)
    && hasOnlyKeys(value, [
      "schemaVersion", "pluginId", "pluginVersion", "pluginHash", "serverId",
      "executable", "argv", "envNames", "permissionProfile",
      "startupTimeoutSeconds", "toolTimeoutSeconds", "declaredEnabled",
      "consented", "available", "errorCode", "updatedAt",
    ])
    && value.schemaVersion === 1
    && ["pluginId", "pluginVersion", "pluginHash", "serverId", "executable"].every(
      (key) => typeof value[key] === "string",
    )
    && Array.isArray(value.argv) && value.argv.every((item) => typeof item === "string")
    && Array.isArray(value.envNames) && value.envNames.every((item) => typeof item === "string")
    && ["connector", "workspace_read"].includes(String(value.permissionProfile))
    && isPositiveInteger(value.startupTimeoutSeconds)
    && isPositiveInteger(value.toolTimeoutSeconds)
    && typeof value.declaredEnabled === "boolean"
    && typeof value.consented === "boolean"
    && typeof value.available === "boolean"
    && (value.errorCode === undefined || typeof value.errorCode === "string")
    && isNonNegativeInteger(value.updatedAt)
  );
}

function isMcpServerListResult(value: unknown): value is { servers: McpServerRecord[] } {
  return isRecord(value) && hasOnlyKeys(value, ["servers"])
    && Array.isArray(value.servers) && value.servers.every(isMcpServerRecord);
}

function isExtensionSnapshotResult(value: unknown): value is ExtensionSnapshotResult {
  return (
    isRecord(value)
    && hasOnlyKeys(value, ["plugins", "skills", "servers", "throughEventId"])
    && Array.isArray(value.plugins) && value.plugins.every(isPluginRecord)
    && Array.isArray(value.skills) && value.skills.every(isSkillMetadata)
    && Array.isArray(value.servers) && value.servers.every(isMcpServerRecord)
    && isNonNegativeInteger(value.throughEventId)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  const allowedKeys = new Set(allowed);
  return Object.keys(value).every((key) => allowedKeys.has(key));
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) > 0;
}

function approvalRequestFrom(
  message: { jsonrpc: "2.0"; id: string; method: string; params: unknown },
): ApprovalRequest | undefined {
  if (message.method !== "item/requestApproval" || !isRecord(message.params)) {
    return undefined;
  }
  const params = message.params;
  const common = (
    typeof params.sessionId === "string"
    && typeof params.runId === "string"
    && typeof params.itemId === "string"
    && typeof params.toolCallId === "string"
    && typeof params.summary === "string"
  );
  if (!common) {
    return undefined;
  }
  if (params.kind === "file_change") {
    if (typeof params.diff !== "string") {
      return undefined;
    }
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "file_change",
      summary: params.summary as string,
      diff: params.diff as string,
    };
  }
  if (params.kind === "external_tool") {
    const provenance = projectApprovalToolProvenance(params.provenance);
    if (
      typeof params.toolName !== "string"
      || !isRecord(params.arguments)
      || provenance === undefined
      || !["connector", "workspace_read"].includes(String(params.permissionProfile))
      || !isPositiveInteger(params.timeoutSeconds)
      || !Array.isArray(params.envNames)
      || !params.envNames.every((name) => typeof name === "string")
    ) {
      return undefined;
    }
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "external_tool",
      summary: params.summary as string,
      toolName: params.toolName as string,
      arguments: params.arguments as Record<string, unknown>,
      provenance,
      permissionProfile: params.permissionProfile as "connector" | "workspace_read",
      timeoutSeconds: params.timeoutSeconds as number,
      envNames: [...(params.envNames as string[])],
    };
  }
  if (params.kind === "network_access") {
    if (
      typeof params.toolName !== "string"
      || !Array.isArray(params.hosts)
      || params.hosts.length === 0
      || !params.hosts.every((host) => typeof host === "string")
      || typeof params.target !== "string"
    ) {
      return undefined;
    }
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "network_access",
      summary: params.summary as string,
      toolName: params.toolName as string,
      hosts: [...(params.hosts as string[])],
      target: params.target as string,
    };
  }
  if (
    params.kind === "command_execution"
    && typeof params.command === "string"
    && typeof params.cwd === "string"
    && typeof params.networkEnabled === "boolean"
    && isPositiveInteger(params.timeoutSeconds)
    && (
      params.executionMode === undefined
      || ["default_sandbox", "expanded_sandbox", "unsandboxed"].includes(String(params.executionMode))
    )
    && (
      params.sandboxPermissions === undefined
      || ["use_default", "with_additional_permissions", "require_escalated"].includes(String(params.sandboxPermissions))
    )
    && [params.additionalReadAccess, params.additionalWriteAccess, params.additionalExecutableAccess]
      .every((paths) => paths === undefined || (Array.isArray(paths) && paths.every((path) => typeof path === "string")))
    && (params.reason === undefined || typeof params.reason === "string")
    && (params.escalationReason === undefined || typeof params.escalationReason === "string")
    && (params.attemptOrdinal === undefined || params.attemptOrdinal === 0 || params.attemptOrdinal === 1)
  ) {
    return {
      id: message.id,
      sessionId: params.sessionId as string,
      runId: params.runId as string,
      itemId: params.itemId as string,
      toolCallId: params.toolCallId as string,
      kind: "command_execution",
      summary: params.summary as string,
      command: params.command as string,
      cwd: params.cwd as string,
      networkEnabled: params.networkEnabled as boolean,
      timeoutSeconds: params.timeoutSeconds as number,
      executionMode: (params.executionMode ?? "default_sandbox") as NonNullable<CommandApprovalRequest["executionMode"]>,
      sandboxPermissions: (params.sandboxPermissions ?? "use_default") as NonNullable<CommandApprovalRequest["sandboxPermissions"]>,
      additionalReadAccess: [...(params.additionalReadAccess as string[] | undefined ?? [])],
      additionalWriteAccess: [...(params.additionalWriteAccess as string[] | undefined ?? [])],
      additionalExecutableAccess: [...(params.additionalExecutableAccess as string[] | undefined ?? [])],
      reason: (params.reason as string | undefined) ?? "",
      escalationReason: (params.escalationReason as string | undefined) ?? "",
      attemptOrdinal: (params.attemptOrdinal ?? 0) as 0 | 1,
    };
  }
  return undefined;
}
