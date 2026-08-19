import type {
  Item,
  Run,
  RuntimeNotification,
  RuntimeStatus,
  Project,
  Session,
  SessionSnapshot,
} from "./contracts.js";


export interface ProjectSessionGroup {
  key: string;
  projectId?: string;
  project?: Project;
  projectless: boolean;
  workspaceRoot: string;
  displayName: string;
  gitAvailable: boolean;
  createdAt: number;
  sessions: Session[];
}

export interface TaskStatusPresentation {
  label: string;
  tone: "success" | "progress" | "error";
  spinning: boolean;
}

export function taskStatusPresentation(
  status: Session["taskStatus"],
  completedRead = false,
): TaskStatusPresentation | undefined {
  switch (status) {
    case "completed":
      return completedRead
        ? undefined
        : { label: "未读完成", tone: "success", spinning: false };
    case "in_progress":
      return { label: "进行中", tone: "progress", spinning: true };
    case "failed":
      return { label: "失败", tone: "error", spinning: false };
    case "new":
    case "canceled":
      return undefined;
  }
}

export function taskStatusFromRun(run: Run): Session["taskStatus"] {
  if (["queued", "running", "waiting_approval", "finalizing"].includes(run.status)) {
    return "in_progress";
  }
  if (run.status === "succeeded") {
    return "completed";
  }
  if (["failed", "stopped", "interrupted"].includes(run.status)) {
    return "failed";
  }
  return "canceled";
}

export function groupSessionsByProject(
  sessions: Session[],
  projects: Project[] = [],
): ProjectSessionGroup[] {
  const grouped = new Map<string, Omit<ProjectSessionGroup, "createdAt" | "sessions"> & {
    sessions: Session[];
  }>();

  for (const project of projects) {
    grouped.set(project.id, {
      key: project.id,
      projectId: project.id,
      project,
      projectless: false,
      workspaceRoot: project.workspaceRoot,
      displayName: project.name?.trim() || basename(project.workspaceRoot),
      gitAvailable: project.gitAvailable,
      sessions: [],
    });
  }

  for (const session of sessions) {
    const project = session.project;
    const worktree = session.worktree;
    const projectless = session.projectless === true;
    // The fallback is only for old event fixtures. Runtime session/list and
    // session/read always provide the explicit Project projection.
    const key = projectless ? "projectless" : project?.id ?? `workspace:${session.workspaceRoot}`;
    const workspaceRoot = project?.workspaceRoot ?? (projectless ? "" : session.workspaceRoot);
    const existing = grouped.get(key);
    if (existing) {
      existing.sessions.push(session);
    } else {
      const sessionProject = projectless || project === undefined
        ? undefined
        : {
            id: project.id,
            ...(project.name ? { name: project.name } : {}),
            workspaceRoot: project.workspaceRoot,
            gitAvailable: project.gitAvailable,
            createdAt: session.createdAt,
            updatedAt: session.updatedAt,
          };
      grouped.set(key, {
        key,
        ...(project?.id
          ? { projectId: project.id }
          : worktree?.projectId
            ? { projectId: worktree.projectId }
            : {}),
        ...(sessionProject ? { project: sessionProject } : {}),
        projectless,
        workspaceRoot,
        displayName: projectless ? "最近" : project?.name?.trim() || basename(workspaceRoot),
        gitAvailable: projectless ? false : project?.gitAvailable ?? worktree !== undefined,
        sessions: [session],
      });
    }
  }
  return [...grouped.values()].map((group) => ({
    ...group,
    createdAt: group.project?.createdAt
      ?? Math.min(...group.sessions.map((session) => session.createdAt)),
    sessions: [...group.sessions].sort((left, right) => right.createdAt - left.createdAt),
  })).sort((left, right) => (
    right.createdAt - left.createdAt
    || left.displayName.localeCompare(right.displayName)
  ));
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}


export interface SnapshotReadToken {
  readonly generation: number;
  readonly sessionId: string;
}

export class SnapshotReadCoordinator {
  private generation = 0;
  private selectedSessionId: string | undefined;
  private throughEventId = 0;

  select(sessionId: string): SnapshotReadToken {
    this.selectedSessionId = sessionId;
    this.throughEventId = 0;
    return this.nextToken(sessionId);
  }

  refresh(sessionId: string): SnapshotReadToken | undefined {
    if (this.selectedSessionId !== sessionId) {
      return undefined;
    }
    return this.nextToken(sessionId);
  }

  accept(
    token: SnapshotReadToken,
    snapshot: SessionSnapshot,
  ): SessionSnapshot | undefined {
    if (
      !this.isCurrent(token)
      || snapshot.session.id !== token.sessionId
      || (snapshot.throughEventId ?? 0) < this.throughEventId
    ) {
      return undefined;
    }
    this.throughEventId = snapshot.throughEventId ?? 0;
    return snapshot;
  }

  isCurrent(token: SnapshotReadToken): boolean {
    return (
      token.generation === this.generation
      && token.sessionId === this.selectedSessionId
    );
  }

  private nextToken(sessionId: string): SnapshotReadToken {
    this.generation += 1;
    return { generation: this.generation, sessionId };
  }
}

export function applyNotification(
  snapshot: SessionSnapshot | undefined,
  notification: RuntimeNotification,
): SessionSnapshot | undefined {
  if (!snapshot || notification.params.sessionId !== snapshot.session.id) {
    return snapshot;
  }
  if (notification.method === "session/titleUpdated") {
    return {
      ...snapshot,
      session: {
        ...snapshot.session,
        title: notification.params.title,
      },
    };
  }
  if (
    notification.method === "run/started"
    || notification.method === "run/updated"
    || notification.method === "run/completed"
  ) {
    return { ...snapshot, runs: upsertRun(snapshot.runs, notification.params.run) };
  }
  if (notification.method === "item/delta") {
    return {
      ...snapshot,
      items: snapshot.items.map((item) => item.id === notification.params.itemId
        ? { ...item, content: `${item.content ?? ""}${notification.params.delta}` }
        : item),
    };
  }
  if (
    notification.method === "approval/requested"
    || notification.method === "approval/resolved"
    || notification.method === "approval/canceled"
  ) {
    return snapshot;
  }
  if (!("item" in notification.params)) {
    return snapshot;
  }
  const incoming = notification.params.item;
  const existing = snapshot.items.find((item) => item.id === incoming.id);
  let merged: Item = existing?.content !== undefined && incoming.content === undefined
    ? { ...incoming, content: existing.content }
    : incoming;
  if (existing?.toolCall && incoming.toolCall) {
    merged = { ...merged, toolCall: { ...existing.toolCall, ...incoming.toolCall } };
  }
  return { ...snapshot, items: upsertItem(snapshot.items, merged) };
}

export type RunStatusTone = "success" | "error" | "neutral" | "warning";

export interface RunStatusPresentation {
  label: string;
  tone: RunStatusTone;
}

const RUNTIME_ERROR_MESSAGES: Record<string, string> = {
  RUNTIME_NOT_INITIALIZED: "Runtime 尚未就绪，请稍后重试。",
  PROTOCOL_VERSION_UNSUPPORTED: "桌面端与 Runtime 版本不兼容，请重启或更新 Eidos。",
  RUN_ALREADY_ACTIVE: "当前已有一个 Run 正在执行，请先等待完成或取消。",
  RESOURCE_NOT_FOUND: "请求的 Session、Project 或 Run 已不存在，请刷新后重试。",
  INVALID_STATE: "当前状态不允许执行这个操作，请刷新后重试。",
  APPROVAL_NO_LONGER_PENDING: "这个审批已经失效，无需再次处理。",
  WORKSPACE_BOUNDARY_VIOLATION: "所选路径超出 Workspace 安全边界，操作已拒绝。",
  SANDBOX_UNAVAILABLE: "Shell 沙箱当前不可用，命令未执行。",
  STORAGE_HEALTH_ONLY: "状态存储需要修复；当前仅提供健康检查，未执行任何业务操作。",
  SENSITIVE_CONTENT_REJECTED: "内容包含受保护信息，已在写入或发送前拒绝。",
  SENSITIVE_SCAN_FAILED: "内容安全扫描未完成，原文未被发送或保存。",
  INVALID_SESSION_TITLE: "任务标题不能为空，请换一个标题。",
  SESSION_HAS_ACTIVE_RUN: "任务仍在执行，请先取消或等待完成后再删除。",
  GIT_WORKTREE_NOT_MANAGED: "这个项目没有可用的 Managed Worktree，无法读取 Git 变更。",
  REPOSITORY_NOT_FOUND: "所选工作空间不存在或不是目录。",
  WORKSPACE_IDENTITY_UNAVAILABLE: "工作空间身份无法确认，操作已停止。",
  WORKTREE_CREATE_FAILED: "Managed Worktree 创建失败，请查看 Runtime 日志。",
  WORKTREE_PERSISTENCE_FAILED: "Managed Worktree 状态写入失败，请查看 Runtime 日志。",
  WORKTREE_RECOVERY_REQUIRED: "Managed Worktree 需要恢复，请查看 Runtime 日志。",
  LOCAL_CHANGES_BASE_MISMATCH: "当前工作区的 HEAD 与起始 Ref 不一致，未复制本地修改。",
  WORKTREE_SOURCE_CHANGED: "源工作区在创建过程中发生变化，操作已停止。",
  WORKTREE_LOCAL_CHANGES_CONFLICT: "当前本地修改无法安全应用到 Worktree。",
  WORKTREE_INCLUDE_INVALID: ".worktreeinclude 包含不安全或无效路径。",
  WORKTREE_INCLUDE_FAILED: "环境文件复制失败，Worktree 未完成创建。",
  WORKTREE_REQUIRED: "只有 Worktree 任务可以创建分支。",
  WORKTREE_ALREADY_ATTACHED: "这个 Worktree 已经绑定分支。",
  BRANCH_ALREADY_EXISTS: "这个分支已经存在。",
  WORKTREE_BRANCH_IN_USE: "这个分支已经在其他 Worktree 中使用。",
  BRANCH_INVALID: "分支名无效，请输入合法的 Git 分支名。",
  WORKTREE_BRANCH_CREATE_FAILED: "分支创建失败，请查看 Runtime 日志。",
  WORKTREE_BRANCH_STATE_CHANGED: "Worktree 的分支状态发生变化，请恢复后重试。",
  LOCAL_REQUIRED: "只有 Local 任务可以切换或创建本地分支。",
  CHECKPOINT_GIT_STATE_UNAVAILABLE: "Checkpoint 的 Git 状态不可用。",
  CHECKPOINT_FORK_WORKTREE_FAILED: "Checkpoint Fork 的 Managed Worktree 创建失败。",
  CHECKPOINT_REWIND_FAILED: "Checkpoint Rewind 无法恢复 Git 工作区状态。",
  CHECKPOINT_WORKFLOW_BUSY: "当前任务仍在运行。请等待或取消 Run 后再恢复 Checkpoint。",
  DIRECT_CHECKPOINT_FORK_PATH_FORBIDDEN: "Direct Workspace Fork 使用原 Project 工作空间。",
  MANAGED_CHECKPOINT_FORK_PATH_FORBIDDEN: "Managed Worktree Fork 不接受外部工作空间路径。",
  GIT_OBSERVATION_UNAVAILABLE: "Git 状态暂时无法完整读取，请稍后重试。",
  GIT_WORKTREE_NOT_FOUND: "任务绑定的 Worktree 记录已不存在。",
  GIT_WORKTREE_MISSING: "任务绑定的 Worktree 目录已不存在。",
  GIT_WORKTREE_INVALID: "任务绑定的 Worktree 已失效，请停止在其中执行任务。",
  GIT_REVIEW_FAILED: "Git 变更读取失败，请查看 Runtime 日志。",
  GIT_BRANCH_REQUIRED: "请先把当前 Worktree 绑定到分支，再执行该 Git 操作。",
  GIT_BRANCH_NOT_FOUND: "本地分支不存在，请刷新后重试。",
  GIT_BRANCH_SWITCH_FAILED: "本地分支切换失败，请刷新后重试。",
  GIT_BRANCH_CREATE_FAILED: "本地分支创建失败，请查看 Runtime 日志。",
  GIT_WORKFLOW_BUSY: "当前任务仍在运行。请等待或取消 Run 后再执行 Git 操作。",
  GIT_NOTHING_STAGED: "当前没有已 Stage 的改动可以提交。",
  GIT_IDENTITY_UNAVAILABLE: "Git commit identity 不可用。请配置 user.name 和 user.email。",
  GIT_CONFLICT: "Git Index 仍有未解决冲突。请先处理冲突。",
  GIT_REMOTE_REQUIRED: "当前仓库有多个 Remote。请先配置当前分支的 upstream。",
  GIT_REMOTE_NOT_FOUND: "所选 Git Remote 已不存在。请刷新后重试。",
  GIT_UPSTREAM_NOT_FOUND: "当前分支没有可用的 upstream。",
  GIT_REMOTE_UNSUPPORTED: "当前 Remote transport 不受 Eidos 支持。",
  GIT_REMOTE_TIMEOUT: "Remote Git 操作超时。请检查网络后重试。",
  GIT_REMOTE_CANCELED: "Remote Git 操作已取消。",
  GIT_REMOTE_FAILED: "Remote Git 操作失败。请检查系统 Git 凭据和 Runtime 日志。",
  GIT_REMOTE_OUTCOME_UNCERTAIN: "上一次 Git 操作可能已产生外部变更。请先刷新并检查 Git/远端状态；再次执行将作为新的 Git 操作。",
  GIT_WORKTREE_DIRTY: "当前 Workspace 有未提交改动。该操作要求干净工作区。",
  GIT_REMOTE_BEHIND: "本地分支落后于 Remote。请先 Pull。",
  GIT_REMOTE_DIVERGED: "本地分支与 Remote 已分叉。Eidos 不会自动合并或 Rebase。",
  GIT_OPERATION_IN_PROGRESS: "该 Git 操作仍在进行。请刷新状态后继续。",
  GIT_MERGE_NOT_IN_PROGRESS: "当前没有进行中的 Merge。",
  GIT_MERGE_TARGET_INVALID: "Merge target 无效。请刷新 branch 列表。",
  GIT_REBASE_NOT_IN_PROGRESS: "当前没有进行中的 Rebase。",
  GIT_REBASE_TARGET_INVALID: "Rebase target 无效。请刷新 branch 列表。",
  REVIEW_DIFF_CHANGED: "文件 Diff 已变化。请刷新后重新添加 Review Comment。",
  REVIEW_ANCHOR_INVALID: "Review Comment 的行位置已无效，请重新选择 Diff 行。",
  REVIEW_COMMENT_ID_REUSED: "Review Comment 标识已被使用。请重试。",
  REVIEW_COMMENT_NOT_FOUND: "Review Comment 已不存在。请刷新后重试。",
  WORKSPACE_IDENTITY_CHANGED: "任务目录的身份已经变化，Run 未启动。请刷新后重试。",
  WORKTREE_DIRTY: "任务仍有未提交或冲突的变更，不能删除。",
  WORKTREE_DELETE_FAILED: "任务的 Worktree 删除失败，请查看 Runtime 日志后重试。",
  SESSION_PERSISTENCE_FAILED: "任务状态写入失败。Worktree 已保留或可安全重试。",
  PROJECT_HAS_SESSIONS: "项目下还有任务，请先删除任务后再删除项目。",
  PROJECT_WORKTREE_RECOVERY_REQUIRED: "项目的 Managed Worktree 仍需恢复，暂时不能删除项目。",
  PROJECT_PERSISTENCE_FAILED: "项目状态写入失败，请查看 Runtime 日志。",
  MODEL_NOT_AVAILABLE: "所选模型当前不可用，请重新选择。",
  INTERNAL_ERROR: "Runtime 遇到内部错误，请查看诊断日志。",
};

const STOP_REASON_MESSAGES: Record<string, string> = {
  // Legacy persisted stop reasons; Runtime no longer emits these.
  max_total_steps: "已达到任务执行预算",
  max_effective_runtime: "已达到最长执行时间",
  segment_step_limit: "已达到单段任务执行预算",
  segment_time_limit: "已达到单段执行时间",
  context_still_over_budget: "上下文容量已达到上限",
  repeated_tool_call: "检测到重复工具调用，任务已停止",
  // Legacy persisted stop reason; convergence no longer emits error counts.
  repeated_tool_error: "工具持续失败，任务已停止",
  no_progress: "恢复后仍返回相同执行状态，任务已停止",
  repeated_empty_response: "模型连续返回空响应，任务已停止",
  repeated_sensitive_tool_input: "连续工具输入被安全策略拒绝，任务已停止",
};

export function runtimeBusinessCode(cause: unknown): string | undefined {
  const message = cause instanceof Error ? cause.message : "";
  const match = message.match(/EIDOS_RUNTIME_ERROR:([A-Z_]+)/);
  return match?.[1];
}

export function userFacingError(cause: unknown): string {
  const code = runtimeBusinessCode(cause);
  if (code) return RUNTIME_ERROR_MESSAGES[code] ?? "Runtime 遇到内部错误，请查看诊断日志。";
  if (cause instanceof Error && cause.message === "这个审批已经失效。") {
    return cause.message;
  }
  return "操作失败，请查看 Runtime 日志。";
}

export function terminalRunPresentation(
  run: Run,
): RunStatusPresentation | undefined {
  switch (run.status) {
    case "succeeded":
      return { label: "已完成", tone: "success" };
    case "failed":
      return {
        label: `失败：${run.errorCode ?? "UNKNOWN_ERROR"}`,
        tone: "error",
      };
    case "canceled":
      return { label: "已取消", tone: "neutral" };
    case "interrupted":
      return { label: "已中断，未自动恢复", tone: "warning" };
    case "stopped":
      return {
        label: run.stopReason
          ? STOP_REASON_MESSAGES[run.stopReason] ?? "任务已停止"
          : "任务已停止",
        tone: "warning",
      };
    case "queued":
    case "finalizing":
    case "running":
    case "waiting_approval":
      return undefined;
  }
}

// ---------------------------------------------------------------------------
// ComposerMode state machine
// ---------------------------------------------------------------------------

/**
 * Represents the UI state of the Composer input area.
 *
 * State constraints:
 * - A session MUST have at most one activeRun at a time.
 * - `starting` is a transient state while the IPC call is in-flight.
 * - `read_only` takes precedence over all run-based states.
 * - `idle` is the only state where a new Run can be started.
 */
export type ComposerMode =
  | "idle"               // No active run; can input and start
  | "starting"           // startRun IPC call in-flight; block double submit
  | "running"            // Run is executing; show cancel if allowed
  | "waiting_approval"   // Approval card is the primary entry point
  | "finalizing"         // Run wrapping up; block all input
  | "read_only";         // storageHealth = health_only; block all writes

const ACTIVE_RUN_STATUSES = new Set<Run["status"]>([
  "queued", "running", "waiting_approval", "finalizing",
]);

/**
 * Derives the Composer UI mode from observable state.
 * This is a pure function — no side effects, fully testable.
 *
 * @param storageHealthy - true when storageHealth.state === "ready"
 * @param activeRun - the active (non-terminal) run for the current session, if any
 * @param isStarting - true while startRun IPC call is in-flight
 */
export function deriveComposerMode(
  storageHealthy: boolean,
  activeRun: Run | undefined,
  isStarting: boolean,
): ComposerMode {
  // read_only takes precedence over everything
  if (!storageHealthy) return "read_only";

  // Transient starting state (IPC in-flight, no activeRun yet)
  if (isStarting && !activeRun) return "starting";

  if (!activeRun) return "idle";

  // Map Run status → ComposerMode
  switch (activeRun.status) {
    case "waiting_approval":
      return "waiting_approval";
    case "finalizing":
      return "finalizing";
    case "queued":
    case "running":
    default:
      return "running";
  }
}

/**
 * Find the active (non-terminal) run in a list of runs.
 * Returns the most recent active run (last in array order).
 */
export function findActiveRun(runs: Run[]): Run | undefined {
  return [...runs].reverse().find((run) => ACTIVE_RUN_STATUSES.has(run.status));
}

// ---------------------------------------------------------------------------
// Runtime Presentation — unified across Sidebar, Settings, and RuntimeGate
// ---------------------------------------------------------------------------

/**
 * A unified presentation record for the Runtime connection state.
 * Used in Sidebar indicator, RuntimeSettings, and the startup gate.
 */
export interface RuntimePresentation {
  /** Short label, accessible text for status dot */
  label: string;
  /** Colour tone for the indicator */
  tone: "success" | "warning" | "danger" | "neutral";
  /** Optional extended description */
  description?: string;
  /** Whether to show an animation (connecting, reconnecting) */
  animated?: boolean;
}

export function deriveRuntimePresentation(status: RuntimeStatus): RuntimePresentation {
  switch (status.state) {
    case "starting":
      return {
        label: "正在启动",
        tone: "neutral",
        description: "正在建立安全沙箱与 Runtime 协议握手…",
        animated: true,
      };
    case "error":
      return {
        label: "连接失败",
        tone: "danger",
        description: status.message,
      };
    case "ready": {
      const health = status.storageHealth;
      if (health.state === "health_only") {
        return {
          label: "只读模式",
          tone: "warning",
          description: `状态存储处于只读健康模式（${health.code ?? "unknown"}），不会执行 Run 或写入状态。`,
        };
      }
      return {
        label: "Runtime 就绪",
        tone: "success",
      };
    }
  }
}


export function upsertRun(runs: Run[], incoming: Run): Run[] {
  const existingIndex = runs.findIndex((run) => run.id === incoming.id);
  if (existingIndex < 0) {
    return [...runs, incoming];
  }
  const existing = runs[existingIndex];
  if (existing && incoming.updatedAt < existing.updatedAt) {
    // Ignore stale incoming run
    return runs;
  }
  return runs.map((run, index) => index === existingIndex ? incoming : run);
}

function upsertItem(items: Item[], incoming: Item): Item[] {
  const existing = items.findIndex((item) => item.id === incoming.id);
  if (existing < 0) {
    return [...items, incoming];
  }
  return items.map((item, index) => index === existing ? incoming : item);
}
