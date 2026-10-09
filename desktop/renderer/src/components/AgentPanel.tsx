import { useEffect, useMemo, useState } from "react";
import type { AgentSummary, CollaborationState } from "../../../shared/collaboration.generated.js";
import type { ApprovalRequest, Item, Run, SessionSnapshot } from "../contracts.js";
import { ApprovalComposer } from "./ApprovalComposer.js";
import { ExecutionFeed, formatItemTime } from "./ExecutionFeed.js";
import { userFacingError } from "../session-state.js";
import "./agents.css";

const active = new Set([
  "queued",
  "running",
  "waiting_approval",
  "waiting_input",
  "waiting_agents",
  "finalizing",
]);

const labels: Record<AgentSummary["status"], string> = {
  queued: "排队中",
  running: "执行中",
  waiting_approval: "等待批准",
  waiting_input: "等待回答",
  waiting_agents: "等待子任务",
  finalizing: "收尾中",
  succeeded: "已完成",
  failed: "失败",
  stopped: "已停止",
  canceled: "已取消",
  interrupted: "已中断",
};

const roleLabels: Record<AgentSummary["role"], string> = {
  default: "default",
  explorer: "explorer",
  worker: "worker",
};

const roleDescriptions: Record<AgentSummary["role"], string> = {
  default: "default",
  explorer: "explorer",
  worker: "worker",
};

export function useAgentState(sessionId: string | undefined, ready: boolean) {
  const [state, setState] = useState<CollaborationState>();
  const [error, setError] = useState("");
  const [stopping, setStopping] = useState<string>();

  useEffect(() => {
    setState(undefined);
    setError("");
    if (!sessionId || !ready || typeof window.eidosRuntime?.readAgents !== "function") return;
    let disposed = false;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = () => {
      const current = ++generation;
      void window.eidosRuntime.readAgents(sessionId).then((value) => {
        if (!disposed && generation === current) {
          setState(value);
          setError("");
        }
      }).catch((cause: unknown) => {
        if (!disposed && generation === current) setError(userFacingError(cause));
      });
    };

    load();
    const unsubscribe = window.eidosRuntime.onNotification(() => {
      if (timer === undefined) {
        timer = setTimeout(() => {
          timer = undefined;
          load();
        }, 250);
      }
    });

    return () => {
      disposed = true;
      clearTimeout(timer);
      unsubscribe();
    };
  }, [sessionId, ready]);

  const stop = async (agent: AgentSummary) => {
    setStopping(agent.id);
    try {
      const value = await window.eidosRuntime.stopAgent(agent.parentRunId, agent.id);
      setState(value);
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setStopping(undefined);
    }
  };

  return { state, error, stopping, stop };
}

const errorCodeLabels: Record<string, string> = {
  RUNTIME_INTERRUPTED: "运行时中断",
  completion_unconfirmed: "未确认完成",
  agent_failed: "执行失败",
  agent_task_limit: "任务超限",
  agent_task_name_exists: "任务名冲突",
  agent_operation_conflict: "操作冲突",
  nested_delegation_not_supported: "不支持嵌套委派",
  timeout: "超时",
  canceled: "已取消",
  interrupted: "已中断",
  stopped: "已停止",
};

export function formatErrorCode(code: string | null | undefined): string {
  if (!code) return "";
  return errorCodeLabels[code] ?? code.replace(/_/g, " ");
}

export function statusBadgeTone(status: AgentSummary["status"]): "success" | "warning" | "danger" | "neutral" | "active" {
  switch (status) {
    case "running":
    case "finalizing":
    case "queued":
      return "active";
    case "waiting_approval":
    case "waiting_input":
    case "waiting_agents":
      return "warning";
    case "succeeded":
      return "success";
    case "failed":
    case "interrupted":
      return "danger";
    case "stopped":
    case "canceled":
    default:
      return "neutral";
  }
}

export function formatAgentTooltip(agent: AgentSummary): string {
  const roleName = roleLabels[agent.role] ?? agent.role;
  const parts: string[] = [
    `${agent.taskName} (${roleName} · ${labels[agent.status]})`,
  ];
  if (agent.task?.trim()) {
    parts.push(`任务描述：${agent.task.trim()}`);
  }
  if (agent.result?.trim()) {
    parts.push(`交付成果：${agent.result.trim()}`);
  }
  if (agent.errorCode?.trim()) {
    parts.push(`错误代码：${formatErrorCode(agent.errorCode)}`);
  }
  return parts.join("\n");
}

export function AgentList({
  agents,
  onOpen,
  approvalCounts = {},
  selectedAgentId,
  compact = false,
}: {
  agents: AgentSummary[];
  onOpen: (agent: AgentSummary) => void;
  approvalCounts?: Readonly<Record<string, number>>;
  selectedAgentId?: string | undefined;
  compact?: boolean;
}) {
  return (
    <div className={`agent-list${compact ? " agent-list--compact" : ""}`} role="list">
      {agents.map((agent) => {
        const pendingApprovals = approvalCounts[agent.sessionId] ?? 0;
        const isRunning = active.has(agent.status);
        const isSelected = selectedAgentId === agent.id;
        const tooltip = formatAgentTooltip(agent);

        if (compact) {
          return (
            <button
              key={agent.id}
              type="button"
              className={`environment-popover__row agent-list__row agent-list__row--compact${isSelected ? " agent-list__row--selected" : ""}${isRunning ? " agent-list__row--running" : ""}`}
              onClick={() => onOpen(agent)}
              title={tooltip}
              aria-label={`${agent.taskName} · ${labels[agent.status]}${pendingApprovals ? ` · 待审批 ${pendingApprovals}` : ""}`}
            >
              <div className="agent-list__row-main">
                <span className={`agent-status-dot agent-status-dot--${agent.status}`} aria-hidden="true" />
                <span className="agent-list__row-name">{agent.taskName}</span>
              </div>
              <div className="agent-list__row-status">
                <span className="agent-list__row-status-text">
                  {labels[agent.status]}{pendingApprovals ? ` · 待审批 ${pendingApprovals}` : ""}
                </span>
                {pendingApprovals > 0 && (
                  <span className="agent-approval-badge" aria-label={`待审批 ${pendingApprovals}`}>
                    {pendingApprovals}
                  </span>
                )}
              </div>
            </button>
          );
        }

        return (
          <button
            key={agent.id}
            type="button"
            className={`agent-list__row agent-card${isSelected ? " agent-card--selected" : ""}${isRunning ? " agent-card--running" : ""}`}
            onClick={() => onOpen(agent)}
            title={tooltip}
          >
            <div className="agent-card__header">
              <div className="agent-card__identity">
                <span className={`agent-status-dot agent-status-dot--${agent.status}`} aria-hidden="true" />
                <span className="agent-card__name">{agent.taskName}</span>
                <span className={`agent-role-pill agent-role-pill--${agent.role}`}>
                  {roleLabels[agent.role]}
                </span>
              </div>
              <div className="agent-card__status-wrap">
                <span className={`agent-status-pill agent-status-pill--${statusBadgeTone(agent.status)}`}>
                  {labels[agent.status]}{pendingApprovals ? ` · 待审批 ${pendingApprovals}` : ""}
                </span>
                {pendingApprovals > 0 && (
                  <span className="agent-approval-badge" aria-label={`待审批 ${pendingApprovals}`}>
                    {pendingApprovals}
                  </span>
                )}
              </div>
            </div>

            {agent.task && (
              <div className="agent-card__body">
                <p className="agent-card__snippet">{agent.task.replace(/\s+/g, " ").trim()}</p>
              </div>
            )}

            <div className="agent-card__footer">
              <span className="agent-card__timestamp">
                {formatItemTime(agent.createdAt)}
              </span>
              {agent.result && (
                <span className="agent-card__pill agent-card__pill--success">
                  <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <polyline points="2.5 6 4.5 8 9.5 3" />
                  </svg>
                  成果已交付
                </span>
              )}
              {agent.errorCode && (
                <span className="agent-card__pill agent-card__pill--danger" title={agent.errorCode}>
                  {formatErrorCode(agent.errorCode)}
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function AgentTranscript({
  agent,
  approvals,
  respondingApprovalIds = new Set(),
  respondingKindByApprovalId = {},
  expiredApprovalIds = new Set(),
  errorsByApprovalId = {},
  onApprove,
  onReject,
  onOpenFile,
  onOpenPlan,
  workspaceRoot,
}: {
  agent: AgentSummary;
  approvals: ApprovalRequest[];
  respondingApprovalIds?: ReadonlySet<string>;
  respondingKindByApprovalId?: Readonly<Record<string, "approve" | "reject">>;
  expiredApprovalIds?: ReadonlySet<string>;
  errorsByApprovalId?: Readonly<Record<string, string>>;
  onApprove: (request: ApprovalRequest) => void;
  onReject: (request: ApprovalRequest) => void;
  onOpenFile?: ((path: string) => void) | undefined;
  onOpenPlan?: (() => void) | undefined;
  workspaceRoot?: string | undefined;
}) {
  const [snapshot, setSnapshot] = useState<SessionSnapshot>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async (older = false) => {
    setLoading(true);
    try {
      const value = await window.eidosRuntime.readSession(
        agent.sessionId,
        older && snapshot?.previousItemId
          ? { itemLimit: 50, beforeItemId: snapshot.previousItemId }
          : { itemLimit: 50 },
      );
      setSnapshot((previous) => (older && previous ? { ...value, items: [...value.items, ...previous.items] } : value));
      setError("");
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = window.eidosRuntime.onNotification(() => {
      if (timer === undefined) {
        timer = setTimeout(() => {
          timer = undefined;
          void load();
        }, 250);
      }
    });
    return () => {
      clearTimeout(timer);
      unsubscribe();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.sessionId, agent.runId]);

  const childApprovals = useMemo(
    () => approvals.filter((request) => request.sessionId === agent.sessionId),
    [approvals, agent.sessionId],
  );

  const fallbackRun = useMemo<Run>(() => ({
    id: agent.runId,
    sessionId: agent.sessionId,
    status: agent.status,
    allowedActions: active.has(agent.status) ? ["cancel"] : [],
    modelId: "agent-model",
    modelStepCount: 1,
    createdAt: agent.createdAt,
    updatedAt: agent.createdAt,
  }), [agent.runId, agent.sessionId, agent.status, agent.createdAt]);

  const runs = useMemo(() => {
    if (!snapshot?.runs || snapshot.runs.length === 0) return [fallbackRun];
    if (!snapshot.runs.some((run) => run.id === agent.runId)) {
      return [...snapshot.runs, fallbackRun];
    }
    return snapshot.runs;
  }, [snapshot?.runs, fallbackRun, agent.runId]);

  const displayItems = useMemo(() => {
    const rawItems = snapshot?.items ?? [];
    const hasUserMsg = rawItems.some((item) => item.kind === "user_message");
    const items: Item[] = hasUserMsg
      ? rawItems.map((item) => {
          if (item.kind === "user_message" && item.content) {
            const cleaned = item.content.replace(/^Delegated task from the parent agent\.[^\n]*\n\n/, "");
            return cleaned !== item.content ? { ...item, content: cleaned } : item;
          }
          return item;
        })
      : [
          {
            id: `mission-${agent.id}`,
            sessionId: agent.sessionId,
            runId: agent.runId,
            kind: "user_message" as const,
            ordinal: 0,
            status: "completed" as const,
            content: agent.task,
            createdAt: agent.createdAt,
          },
          ...rawItems,
        ];
    return items;
  }, [snapshot?.items, agent.id, agent.sessionId, agent.runId, agent.task, agent.createdAt]);

  return (
    <section className="agent-transcript" aria-label="子 Agent 执行记录">
      {childApprovals.length > 0 && (
        <div className="agent-transcript__approvals">
          <h3>来自子 Agent：{agent.taskName} 的审批</h3>
          {childApprovals.map((request) => {
            const run = runs.find((entry) => entry.id === request.runId) ?? fallbackRun;
            return run ? (
              <ApprovalComposer
                key={request.id}
                run={run}
                approval={request}
                respondingApprovalIds={respondingApprovalIds}
                respondingKindByApprovalId={respondingKindByApprovalId}
                expiredApprovalIds={expiredApprovalIds}
                errorsByApprovalId={errorsByApprovalId}
                onApprove={onApprove}
                onReject={onReject}
              />
            ) : null;
          })}
        </div>
      )}

      {snapshot?.previousItemId && (
        <div className="agent-transcript__toolbar">
          <button
            type="button"
            className="agent-transcript__action-btn"
            disabled={loading}
            onClick={() => void load(true)}
          >
            加载更早记录
          </button>
        </div>
      )}

      {error && <p className="agent-error-alert" role="alert">{error}</p>}

      <div className="agent-transcript__feed-wrapper">
        <ExecutionFeed
          items={displayItems}
          runs={runs}
          stepResolutions={snapshot?.stepResolutions}
          approvals={childApprovals}
          respondingApprovalIds={respondingApprovalIds}
          respondingKindByApprovalId={respondingKindByApprovalId}
          expiredApprovalIds={expiredApprovalIds}
          errorsByApprovalId={errorsByApprovalId}
          onApprove={onApprove}
          onReject={onReject}
          onOpenFile={onOpenFile}
          onOpenPlan={onOpenPlan}
          workspaceRoot={workspaceRoot ?? ""}
          allowFeedback={false}
          allowRegenerate={false}
          allowEditResend={false}
        />
      </div>
    </section>
  );
}

export function AgentWorkspacePanel({
  agents,
  agentId,
  error,
  stopping,
  onOpen,
  onStop,
  approvals,
  respondingApprovalIds = new Set(),
  respondingKindByApprovalId = {},
  expiredApprovalIds = new Set(),
  errorsByApprovalId = {},
  onApprove,
  onReject,
  onOpenFile,
  onOpenPlan,
  workspaceRoot,
  onBackToList,
}: {
  agents: AgentSummary[];
  agentId: string | undefined;
  error: string;
  stopping: string | undefined;
  onOpen: (agent: AgentSummary) => void;
  onStop: (agent: AgentSummary) => void;
  approvals: ApprovalRequest[];
  respondingApprovalIds?: ReadonlySet<string>;
  respondingKindByApprovalId?: Readonly<Record<string, "approve" | "reject">>;
  expiredApprovalIds?: ReadonlySet<string>;
  errorsByApprovalId?: Readonly<Record<string, string>>;
  onApprove: (request: ApprovalRequest) => void;
  onReject: (request: ApprovalRequest) => void;
  onOpenFile?: ((path: string) => void) | undefined;
  onOpenPlan?: (() => void) | undefined;
  workspaceRoot?: string | undefined;
  onBackToList?: (() => void) | undefined;
}) {
  const [filter, setFilter] = useState<"all" | "active" | "approvals" | "completed">("all");
  const agent = agents.find((entry) => entry.id === agentId);

  const approvalCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const req of approvals) {
      counts[req.sessionId] = (counts[req.sessionId] ?? 0) + 1;
    }
    return counts;
  }, [approvals]);

  const filteredAgents = useMemo(() => {
    if (filter === "active") return agents.filter((a) => active.has(a.status));
    if (filter === "approvals") return agents.filter((a) => (approvalCounts[a.sessionId] ?? 0) > 0);
    if (filter === "completed") return agents.filter((a) => a.status === "succeeded");
    return agents;
  }, [agents, filter, approvalCounts]);

  const runningCount = useMemo(() => agents.filter((a) => active.has(a.status)).length, [agents]);
  const pendingApprovalCount = useMemo(() => agents.filter((a) => (approvalCounts[a.sessionId] ?? 0) > 0).length, [agents, approvalCounts]);
  const completedCount = useMemo(() => agents.filter((a) => a.status === "succeeded").length, [agents]);

  return (
    <section className="agent-workspace" aria-label="子 Agent 工作区">
      {error && <p className="agent-error-banner" role="alert">{error}</p>}

      {!agent && (
        <div className="agent-workspace__overview">
          <header className="agent-overview-header">
            <div className="agent-overview-header__title-row">
              <h2>子 Agent</h2>
              <span className="agent-overview-count">{agents.length} 个任务</span>
            </div>

            {agents.length > 0 && (
              <div className="agent-filter-pills" role="tablist" aria-label="筛选任务">
                <button
                  type="button"
                  className={`agent-filter-pill${filter === "all" ? " agent-filter-pill--active" : ""}`}
                  onClick={() => setFilter("all")}
                >
                  全部 ({agents.length})
                </button>
                {runningCount > 0 && (
                  <button
                    type="button"
                    className={`agent-filter-pill${filter === "active" ? " agent-filter-pill--active" : ""}`}
                    onClick={() => setFilter("active")}
                  >
                    进行中 ({runningCount})
                  </button>
                )}
                {pendingApprovalCount > 0 && (
                  <button
                    type="button"
                    className={`agent-filter-pill agent-filter-pill--warning${filter === "approvals" ? " agent-filter-pill--active" : ""}`}
                    onClick={() => setFilter("approvals")}
                  >
                    待审批 ({pendingApprovalCount})
                  </button>
                )}
                <button
                  type="button"
                  className={`agent-filter-pill${filter === "completed" ? " agent-filter-pill--active" : ""}`}
                  onClick={() => setFilter("completed")}
                >
                  已完成{completedCount > 0 ? ` (${completedCount})` : ""}
                </button>
              </div>
            )}
          </header>

          {agents.length === 0 ? (
            <div className="agent-empty-state">
              <div className="agent-empty-state__icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="36" height="36" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="4" width="18" height="16" rx="3" />
                  <circle cx="9" cy="10" r="1.5" />
                  <circle cx="15" cy="10" r="1.5" />
                  <path d="M8 15h8" />
                </svg>
              </div>
              <h3>暂无子 Agent 任务</h3>
              <p>主 Agent 派生的通用任务 Default、代码调查 Explorer 和实现与验证 Worker 会汇总在此处。子任务继承父任务的权限和普通工具。</p>
            </div>
          ) : (
            <AgentList
              agents={filteredAgents}
              onOpen={onOpen}
              approvalCounts={approvalCounts}
              selectedAgentId={agentId}
            />
          )}
        </div>
      )}

      {agent && (
        <article className="agent-detail-view">
          <header className="agent-detail-header">
            {onBackToList && (
              <button
                type="button"
                className="agent-detail-back-btn"
                aria-label="返回子 Agent 列表"
                onClick={onBackToList}
              >
                <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M10 3.5L5.5 8l4.5 4.5" />
                </svg>
                <span>全部 Agent</span>
              </button>
            )}

            <div className="agent-heading">
              <div className="agent-heading__name-wrap">
                <span className={`agent-status-dot agent-status-dot--${agent.status}`} aria-hidden="true" />
                <strong>{agent.taskName}</strong>
                <span className={`agent-role-pill agent-role-pill--${agent.role}`}>
                  {roleDescriptions[agent.role]}
                </span>
              </div>

              <div className="agent-heading__actions">
                <span className={`agent-status-pill agent-status-pill--${statusBadgeTone(agent.status)}`}>
                  {labels[agent.status]}
                </span>

                {active.has(agent.status) && (
                  <button
                    type="button"
                    className="agent-stop-button"
                    disabled={stopping === agent.id}
                    onClick={() => onStop(agent)}
                  >
                    {stopping === agent.id ? "停止中…" : "停止"}
                  </button>
                )}
              </div>
            </div>
          </header>

          {agent.errorCode && (
            <div className="agent-error-tag-box" role="status">
              <span className="agent-error-tag-box__icon">⚠️</span>
              <span>{formatErrorCode(agent.errorCode)}</span>
            </div>
          )}

          <AgentTranscript
            key={agent.runId}
            agent={agent}
            approvals={approvals}
            respondingApprovalIds={respondingApprovalIds}
            respondingKindByApprovalId={respondingKindByApprovalId}
            expiredApprovalIds={expiredApprovalIds}
            errorsByApprovalId={errorsByApprovalId}
            onApprove={onApprove}
            onReject={onReject}
            onOpenFile={onOpenFile}
            onOpenPlan={onOpenPlan}
            workspaceRoot={workspaceRoot}
          />
        </article>
      )}
    </section>
  );
}
