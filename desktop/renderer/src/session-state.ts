import type {
  Item,
  Run,
  RuntimeNotification,
  Session,
  SessionSnapshot,
} from "./contracts.js";


export interface WorkspaceSessionGroup {
  workspaceRoot: string;
  sessions: Session[];
}

export function groupSessionsByWorkspace(sessions: Session[]): WorkspaceSessionGroup[] {
  const grouped = new Map<string, Session[]>();
  for (const session of sessions) {
    const existing = grouped.get(session.workspaceRoot);
    if (existing) {
      existing.push(session);
    } else {
      grouped.set(session.workspaceRoot, [session]);
    }
  }
  return [...grouped].map(([workspaceRoot, groupedSessions]) => ({
    workspaceRoot,
    sessions: groupedSessions,
  }));
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
  RESOURCE_NOT_FOUND: "请求的 Session 或 Run 已不存在，请刷新后重试。",
  INVALID_STATE: "当前状态不允许执行这个操作，请刷新后重试。",
  APPROVAL_NO_LONGER_PENDING: "这个审批已经失效，无需再次处理。",
  WORKSPACE_BOUNDARY_VIOLATION: "所选路径超出 Workspace 安全边界，操作已拒绝。",
  SANDBOX_UNAVAILABLE: "Shell 沙箱当前不可用，命令未执行。",
  STORAGE_HEALTH_ONLY: "状态存储需要修复；当前仅提供健康检查，未执行任何业务操作。",
  SENSITIVE_CONTENT_REJECTED: "内容包含受保护信息，已在写入或发送前拒绝。",
  SENSITIVE_SCAN_FAILED: "内容安全扫描未完成，原文未被发送或保存。",
  INTERNAL_ERROR: "Runtime 遇到内部错误，请查看诊断日志。",
};

export function userFacingError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : "";
  const match = message.match(/EIDOS_RUNTIME_ERROR:([A-Z_]+)/);
  const code = match?.[1];
  if (code) {
    return RUNTIME_ERROR_MESSAGES[code] ?? "Runtime 遇到内部错误，请查看诊断日志。";
  }
  if (message === "这个审批已经失效。") {
    return message;
  }
  return "操作失败，请查看 Runtime 日志。";
}

export function terminalRunPresentation(
  run: Run,
): RunStatusPresentation | undefined {
  switch (run.status) {
    case "succeeded":
      return { label: "Run 已完成", tone: "success" };
    case "failed":
      return {
        label: `Run 失败：${run.errorCode ?? "UNKNOWN_ERROR"}`,
        tone: "error",
      };
    case "canceled":
      return { label: "Run 已取消", tone: "neutral" };
    case "interrupted":
      return { label: "Run 已中断，未自动恢复", tone: "warning" };
    case "stopped":
      return { label: "Run 已达到执行上限", tone: "warning" };
    case "queued":
    case "waiting_user_input":
    case "finalizing":
    case "running":
    case "waiting_approval":
      return undefined;
  }
}

function upsertRun(runs: Run[], incoming: Run): Run[] {
  const existing = runs.findIndex((run) => run.id === incoming.id);
  if (existing < 0) {
    return [...runs, incoming];
  }
  return runs.map((run, index) => index === existing ? incoming : run);
}

function upsertItem(items: Item[], incoming: Item): Item[] {
  const existing = items.findIndex((item) => item.id === incoming.id);
  if (existing < 0) {
    return [...items, incoming];
  }
  return items.map((item, index) => index === existing ? incoming : item);
}
