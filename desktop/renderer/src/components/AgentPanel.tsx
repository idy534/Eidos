import { useEffect, useState } from "react";
import type { AgentSummary, CollaborationState } from "../../../shared/collaboration.generated.js";
import type { ApprovalRequest, SessionSnapshot } from "../contracts.js";
import { ApprovalComposer } from "./ApprovalComposer.js";
import { userFacingError } from "../session-state.js";
import "./agents.css";

const active = new Set(["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing"]);
const labels: Record<AgentSummary["status"], string> = {
  queued: "排队中", running: "执行中", waiting_approval: "等待批准", waiting_input: "等待回答",
  waiting_agents: "等待子任务", finalizing: "收尾中", succeeded: "已完成", failed: "失败",
  stopped: "已停止", canceled: "已取消", interrupted: "已中断",
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
        if (!disposed && generation === current) { setState(value); setError(""); }
      }).catch((cause: unknown) => { if (!disposed && generation === current) setError(userFacingError(cause)); });
    };
    load();
    const unsubscribe = window.eidosRuntime.onNotification(() => {
      if (timer === undefined) timer = setTimeout(() => { timer = undefined; load(); }, 250);
    });
    return () => { disposed = true; clearTimeout(timer); unsubscribe(); };
  }, [sessionId, ready]);
  const stop = async (agent: AgentSummary) => {
    setStopping(agent.id);
    try {
      const value = await window.eidosRuntime.stopAgent(agent.parentRunId, agent.id);
      setState(value);
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setStopping(undefined); }
  };
  return { state, error, stopping, stop };
}

export function AgentList({
  agents, onOpen,
}: { agents: AgentSummary[]; onOpen: (agent: AgentSummary) => void }) {
  return <div className="agent-list">
    {agents.map((agent) => <button key={agent.id} type="button" className="environment-popover__row agent-list__row" onClick={() => onOpen(agent)}>
      <span>{agent.taskName}</span><span>{labels[agent.status]}</span>
    </button>)}
  </div>;
}

function AgentTranscript({ agent, approvals, respondingApprovalIds, respondingKindByApprovalId, expiredApprovalIds, errorsByApprovalId, onApprove, onReject }: {
  agent: AgentSummary;
  approvals: ApprovalRequest[];
  respondingApprovalIds: ReadonlySet<string>;
  respondingKindByApprovalId: Readonly<Record<string, "approve" | "reject">>;
  expiredApprovalIds: ReadonlySet<string>;
  errorsByApprovalId: Readonly<Record<string, string>>;
  onApprove: (request: ApprovalRequest) => void;
  onReject: (request: ApprovalRequest) => void;
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
      setSnapshot((previous) => older && previous ? { ...value, items: [...value.items, ...previous.items] } : value);
      setError("");
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setLoading(false); }
  };
  useEffect(() => {
    void load();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = window.eidosRuntime.onNotification(() => {
      if (timer === undefined) timer = setTimeout(() => { timer = undefined; void load(); }, 250);
    });
    return () => { clearTimeout(timer); unsubscribe(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.sessionId, agent.runId]);
  return <section className="agent-transcript" aria-label="子 Agent 执行记录">
    {approvals.filter((request) => request.sessionId === agent.sessionId).map((request) => {
      const run = snapshot?.runs.find((entry) => entry.id === request.runId);
      return run ? <ApprovalComposer key={request.id} run={run} approval={request}
        respondingApprovalIds={respondingApprovalIds} respondingKindByApprovalId={respondingKindByApprovalId}
        expiredApprovalIds={expiredApprovalIds} errorsByApprovalId={errorsByApprovalId}
        onApprove={onApprove} onReject={onReject} /> : null;
    })}
    <button type="button" disabled={loading} onClick={() => void load()}>刷新记录</button>
    {snapshot?.previousItemId && <button type="button" disabled={loading} onClick={() => void load(true)}>加载更早记录</button>}
    {error && <p role="alert">{error}</p>}
    {snapshot?.items.map((item) => <details key={item.id}>
      <summary>{item.toolCall?.toolName ?? (item.kind === "assistant_message" ? "助手" : "任务")} · {item.status}</summary>
      <pre>{item.content ?? item.toolCall?.resultJson ?? item.toolCall?.argumentsJson ?? "暂无内容"}</pre>
    </details>)}
  </section>;
}

export function AgentWorkspacePanel({
  agents, agentId, error, stopping, onOpen, onStop, approvals, respondingApprovalIds,
  respondingKindByApprovalId, expiredApprovalIds, errorsByApprovalId, onApprove, onReject,
}: {
  agents: AgentSummary[];
  agentId: string | undefined;
  error: string;
  stopping: string | undefined;
  onOpen: (agent: AgentSummary) => void;
  onStop: (agent: AgentSummary) => void;
  approvals: ApprovalRequest[];
  respondingApprovalIds: ReadonlySet<string>;
  respondingKindByApprovalId: Readonly<Record<string, "approve" | "reject">>;
  expiredApprovalIds: ReadonlySet<string>;
  errorsByApprovalId: Readonly<Record<string, string>>;
  onApprove: (request: ApprovalRequest) => void;
  onReject: (request: ApprovalRequest) => void;
}) {
  const agent = agents.find((entry) => entry.id === agentId);
  return <section className="agent-workspace" aria-label="子 Agent 工作区">
    <h2>子 Agent</h2>
    {error && <p role="alert">{error}</p>}
    {!agent && <AgentList agents={agents} onOpen={onOpen} />}
    {agent && <article>
      <div className="agent-heading"><strong>{agent.taskName}</strong><span>{agent.role === "explorer" ? "探索" : "执行"} · {labels[agent.status]}</span>
        {active.has(agent.status) && <button type="button" disabled={stopping === agent.id} onClick={() => onStop(agent)}>停止</button>}
      </div>
      <p className="agent-text">{agent.task}</p>
      {agent.result && <p className="agent-text">{agent.result}</p>}
      {agent.errorCode && <p role="status">{agent.errorCode}</p>}
      <AgentTranscript key={agent.runId} agent={agent} approvals={approvals}
        respondingApprovalIds={respondingApprovalIds} respondingKindByApprovalId={respondingKindByApprovalId}
        expiredApprovalIds={expiredApprovalIds} errorsByApprovalId={errorsByApprovalId}
        onApprove={onApprove} onReject={onReject} />
    </article>}
  </section>;
}
