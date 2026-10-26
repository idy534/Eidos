import { useRef, useState } from "react";
import { formatInlineReferencesAsPlainText } from "../../../shared/inline-references.js";
import type { QueuedMessage as QueuedMessageData, QueuedRunChange } from "../../../shared/queued-runs.js";
import { userFacingError } from "../session-state.js";
import { useQueuedMessages } from "../app/useQueuedMessages.js";
import { EditIcon, MessageQueueIcon, SteerIcon, TrashIcon } from "./MenuIcons.js";
import { ToastContainer } from "./ToastContainer.js";
import { Tooltip } from "./Tooltip.js";
import { Button } from "./Button.js";

export interface QueuedMessagesProps {
  sessionId: string;
  ready: boolean;
  activeRunId?: string | undefined;
  onChanged: (change: QueuedRunChange) => void;
  onRefresh: () => void;
}

export function QueuedMessages({ sessionId, ready, activeRunId, onChanged, onRefresh }: QueuedMessagesProps) {
  const queue = useQueuedMessages(sessionId, ready);
  if (!queue.items.length && !queue.error) return null;
  return (
    <>
      {queue.error && (
        <ToastContainer
          toasts={[{ id: "queue-load-error", message: queue.error, type: "error", duration: 3500 }]}
        />
      )}
      {queue.items.length > 0 && (
        <ol className="queued-messages" aria-label="待发送消息">
          <li role="presentation" className="queued-message-heading">
            <span>待发送消息 · {queue.items.length}{queue.nextSequence != null ? "+" : ""}</span>
            <span>当前任务结束后依次发送</span>
          </li>
          {queue.items.map((message, index) => (
            <QueuedMessage key={message.runId} message={message} position={index + 1} ready={ready} activeRunId={activeRunId}
              onChanged={(change) => { queue.project(change); onChanged(change); }}
              onRefresh={() => { void queue.refresh(); onRefresh(); }} />
          ))}
          {queue.nextSequence != null && <li className="queued-message-more"><Button size="small" disabled={queue.loading} onClick={() => void queue.refresh(queue.nextSequence!)}>加载更多</Button></li>}
        </ol>
      )}
    </>
  );
}

function QueuedMessage({ message, position, ready, activeRunId, onChanged, onRefresh }: {
  message: QueuedMessageData; position: number;
} & Pick<QueuedMessagesProps, "ready" | "activeRunId" | "onChanged" | "onRefresh">) {
  const text = message.userInput;
  const references = message.references;
  const [edit, setEdit] = useState<{ original: string; text: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const lock = useRef(false);
  const plainPreview = formatInlineReferencesAsPlainText(text).trim();
  const preview = plainPreview || references.map((reference) => reference.label).join("、") || "引用消息";

  async function change(kind: "updated" | "deleted" | "steered") {
    if (lock.current || !ready) return;
    if (kind === "updated" && (!edit || (!edit.text.trim() && !references.length))) return;
    if (kind === "steered" && (!activeRunId || edit)) return;
    lock.current = true;
    setBusy(true);
    setError(undefined);
    const request = {
      sessionId: message.sessionId, runId: message.runId,
      expectedUserInput: edit?.original ?? text, operationId: crypto.randomUUID(),
    };
    try {
      const response = kind === "steered"
        ? await window.eidosRuntime.steerQueuedRun({ ...request, targetRunId: activeRunId! })
        : kind === "deleted"
        ? await window.eidosRuntime.deleteQueuedRun(request)
        : await window.eidosRuntime.updateQueuedRun({ ...request, userInput: edit!.text.trim() });
      onChanged(response.change);
      setEdit(undefined);
    } catch (cause) {
      setError(userFacingError(cause));
      onRefresh();
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  return (
    <li className="queued-message">
      <div className="queued-message-row">
        <span className="queued-message-position" title={`第 ${position} 条待发送消息`}>
          <MessageQueueIcon />
          <span className="sr-only">第 {position} 条待发送消息</span>
        </span>
        <button type="button" className="queued-message-preview" title={preview}
          aria-label={`编辑第 ${position} 条排队消息：${preview}`}
          disabled={!ready || busy}
          onClick={() => { setEdit({ original: text, text }); setError(undefined); }}>
          {preview}
        </button>
        <div className="queued-message-actions">
          <Tooltip content="引导当前任务">
            <button type="button" aria-label={`将第 ${position} 条排队消息用于引导当前任务`}
              disabled={!ready || busy || !activeRunId || !!edit} onClick={() => void change("steered")}>
              <SteerIcon />
            </button>
          </Tooltip>
          <Tooltip content="编辑">
            <button type="button" aria-label={`编辑第 ${position} 条排队消息`}
              disabled={!ready || busy}
              onClick={() => { setEdit({ original: text, text }); setError(undefined); }}>
              <EditIcon /><span>编辑</span>
            </button>
          </Tooltip>
          <Tooltip content="删除">
            <button type="button" aria-label={`删除第 ${position} 条排队消息`}
              disabled={!ready || busy} onClick={() => void change("deleted")}>
              <TrashIcon />
            </button>
          </Tooltip>
        </div>
      </div>
      {edit && (
        <form className="queued-message-editor" onSubmit={(event) => { event.preventDefault(); if (!busy) void change("updated"); }}>
          <textarea aria-label={`编辑第 ${position} 条排队消息内容`} autoFocus value={edit.text}
            maxLength={65536} disabled={!ready || busy}
            onChange={(event) => setEdit({ ...edit, text: event.target.value })}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === "Escape" && !busy) { event.preventDefault(); setEdit(undefined); }
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault(); event.currentTarget.form?.requestSubmit();
              }
            }} />
          {references.length > 0 && <p className="queued-message-references">引用：{references.map((reference) => reference.label).join("、")}</p>}
          <div className="queued-message-editor-actions">
            <Button variant="secondary" size="small" type="button" disabled={busy} onClick={() => setEdit(undefined)}>取消</Button>
            <Button variant="primary" size="small" type="submit" disabled={!ready || busy || (!edit.text.trim() && !references.length)}>
              {busy ? "保存中…" : "保存"}
            </Button>
          </div>
        </form>
      )}
      {error && (
        <ToastContainer
          toasts={[{ id: `queued-message-error-${message.runId}`, message: error, type: "error", duration: 3500 }]}
          onDismiss={() => setError(undefined)}
        />
      )}
    </li>
  );
}
