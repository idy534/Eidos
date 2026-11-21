import { useEffect, useRef, useState } from "react";
import type { AgentChanges, AgentSummary } from "../../../shared/collaboration.generated.js";
import { userFacingError } from "../session-state.js";
import { AlertBanner } from "./AlertBanner.js";

const ACTIVE = new Set(["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing"]);

function newOperationId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `agent-message-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function formatAgentUsage(agent: AgentSummary): string | undefined {
  const usage = agent.usage;
  if (!usage) return undefined;
  const total = (usage.inputTokens ?? 0) + (usage.outputTokens ?? 0);
  if (total === 0 && !usage.incomplete) return undefined;
  const value = total >= 1000 ? `${(total / 1000).toFixed(total >= 100_000 ? 0 : 1)}k` : String(total);
  return `${usage.incomplete ? "≥" : ""}${value} tokens`;
}

/** A user instruction goes straight to this child; the parent sees a preview. */
export function AgentMessageComposer({ agent }: { agent: AgentSummary }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // One draft keeps one operation id, so a retried send is applied once.
  const operation = useRef(newOperationId());

  const send = async () => {
    const value = text.trim();
    if (!value || sending) return;
    setSending(true);
    setError("");
    try {
      await window.eidosRuntime.messageAgent({
        operationId: operation.current, parentRunId: agent.parentRunId, agentId: agent.id, text: value,
      });
      operation.current = newOperationId();
      setText("");
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setSending(false);
    }
  };

  return (
    <form className="agent-message-composer" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      {error && <AlertBanner tone="error">{error}</AlertBanner>}
      <div className="agent-message-composer__row">
        <textarea
          aria-label={`发给 ${agent.taskName}`}
          placeholder={`发给 ${agent.taskName}`}
          rows={2}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <button type="submit" disabled={!text.trim() || sending}>{sending ? "发送中…" : "发送"}</button>
      </div>
    </form>
  );
}

/** Changes of an idle child that owns a Worktree, with diff paging and discard. */
export function AgentChangesView({ agent }: { agent: AgentSummary }) {
  const [changes, setChanges] = useState<AgentChanges>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const idle = agent.workspaceMode === "worktree" && !ACTIVE.has(agent.status);

  useEffect(() => {
    setChanges(undefined);
    setError("");
    setConfirming(false);
    if (!idle) return;
    let disposed = false;
    setLoading(true);
    window.eidosRuntime.readAgentChanges({ parentRunId: agent.parentRunId, agentId: agent.id })
      .then((value) => { if (!disposed) setChanges(value); })
      .catch((cause: unknown) => { if (!disposed) setError(userFacingError(cause)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [agent.id, agent.runId, agent.parentRunId, idle]);

  if (!idle) return null;

  const more = async () => {
    if (!changes) return;
    setLoading(true);
    try {
      const page = await window.eidosRuntime.readAgentChanges({
        parentRunId: agent.parentRunId, agentId: agent.id, offsetBytes: changes.diffNextOffset ?? 0,
      });
      setChanges({ ...page, diff: (changes.diff ?? "") + (page.diff ?? "") });
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setLoading(false);
    }
  };

  const discard = async () => {
    setDiscarding(true);
    setError("");
    try {
      await window.eidosRuntime.discardAgentWorkspace(agent.parentRunId, agent.id);
      setChanges(undefined);
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setDiscarding(false);
      setConfirming(false);
    }
  };

  const discarded = changes?.status === "discarded";
  const files = changes?.files ?? [];
  return (
    <section className="agent-changes" aria-label="变更">
      <header className="agent-changes__header">
        <h3>变更{changes ? ` · ${files.length}` : ""}</h3>
        {changes && !discarded && files.length > 0 && (
          confirming ? (
            <span className="agent-changes__confirm">
              <button type="button" className="agent-changes__danger" disabled={discarding} onClick={() => void discard()}>
                {discarding ? "放弃中…" : "确认放弃"}
              </button>
              <button type="button" onClick={() => setConfirming(false)}>取消</button>
            </span>
          ) : (
            <button type="button" className="agent-changes__danger" onClick={() => setConfirming(true)}>放弃</button>
          )
        )}
      </header>
      {error && <AlertBanner tone="error">{error}</AlertBanner>}
      {files.length > 0 && (
        <ul className="agent-changes__files">
          {files.map((file) => (
            <li key={file.path} className={`agent-changes__file agent-changes__file--${file.kind}`}>
              <span className="agent-changes__path">{file.path}</span>
              <span className="agent-changes__stat">
                {file.binary ? "binary" : `+${file.additions ?? 0} −${file.deletions ?? 0}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {changes?.diff && <pre className="agent-changes__diff">{changes.diff}</pre>}
      {changes?.diffHasMore && (
        <button type="button" className="agent-changes__more" disabled={loading} onClick={() => void more()}>
          {loading ? "加载中…" : "加载更多"}
        </button>
      )}
    </section>
  );
}
