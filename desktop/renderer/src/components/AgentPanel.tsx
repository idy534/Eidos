import { useEffect, useState } from "react";
import type { AgentSummary, CollaborationState } from "../../../shared/collaboration.generated.js";
import type { SessionSnapshot } from "../contracts.js";
import { userFacingError } from "../session-state.js";
import "./agents.css";

const active = new Set(["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing"]);
const labels: Record<AgentSummary["status"], string> = {
  queued: "排队中", running: "执行中", waiting_approval: "等待批准", waiting_input: "等待回答",
  waiting_agents: "等待子任务", finalizing: "收尾中", succeeded: "已完成", failed: "失败",
  stopped: "已停止", canceled: "已取消", interrupted: "已中断",
};

function AgentDetails({ agent }: { agent: AgentSummary }) {
  const [snapshot, setSnapshot] = useState<SessionSnapshot>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const load = async (older = false) => {
    setLoading(true);
    try {
      const value = await window.eidosRuntime.readSession(agent.sessionId, {
        itemLimit: 50, beforeItemId: older ? snapshot?.previousItemId : undefined,
      });
      setSnapshot((previous) => older && previous ? { ...value, items: [...value.items, ...previous.items] } : value);
      setError("");
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setLoading(false); }
  };
  return <details onToggle={(event) => { if (event.currentTarget.open && !snapshot && !loading) void load(); }}>
    <summary>查看任务和记录</summary>
    <p className="agent-text">{agent.task}</p>
    <button type="button" disabled={loading} onClick={() => void load()}>刷新记录</button>
    {snapshot?.previousItemId && <button type="button" disabled={loading} onClick={() => void load(true)}>加载更早记录</button>}
    {error && <p role="alert">{error}</p>}
    <div className="agent-transcript">{snapshot?.items.map((item) => <details key={item.id}>
      <summary>{item.toolCall?.toolName ?? (item.kind === "assistant_message" ? "助手" : "用户")} · {item.status}</summary>
      <pre>{item.content ?? item.toolCall?.resultJson ?? item.toolCall?.argumentsJson ?? "暂无内容"}</pre>
    </details>)}</div>
  </details>;
}

export function AgentPanel({ sessionId, ready }: { sessionId: string; ready: boolean }) {
  const [state, setState] = useState<CollaborationState>();
  const [error, setError] = useState("");
  const [stopping, setStopping] = useState<string>();
  useEffect(() => {
    setState(undefined);
    setError("");
    if (!ready) return;
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
    try { await window.eidosRuntime.stopAgent(agent.parentRunId, agent.id); }
    catch (cause) { setError(userFacingError(cause)); }
    finally { setStopping(undefined); }
  };
  if (!state?.agents?.length && !error) return null;
  return <section className="agent-panel" aria-label="只读子任务">
    <h3>只读子任务</h3>
    <p>子任务共享当前工作目录。主任务负责核对和汇总结果。</p>
    {error && <p role="alert">{error}</p>}
    {state?.agents?.map((agent) => <article key={agent.id}>
      <div className="agent-heading"><strong>{agent.taskName}</strong><span>{labels[agent.status]}</span>
        {active.has(agent.status) && <button type="button" disabled={stopping === agent.id} onClick={() => void stop(agent)}>停止</button>}
      </div>
      {agent.result && <p className="agent-text">{agent.result}</p>}
      {agent.errorCode && <p role="status">{agent.errorCode}</p>}
      <AgentDetails key={agent.runId} agent={agent} />
    </article>)}
  </section>;
}
