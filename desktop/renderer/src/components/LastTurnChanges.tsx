import { useState } from "react";
import { Diff, Hunk, parseDiff } from "react-diff-view";
import type { Item } from "../contracts.js";
import { useArtifacts } from "./ArtifactContext.js";
import { userFacingError } from "../session-state.js";

export function LastTurnChanges({ sessionId, previousItemId, items, runId, focusPath, onFeedback, disabled, entireTask = false }: {
  entireTask?: boolean; sessionId: string; previousItemId?: string | undefined; items: Item[]; runId?: string | undefined; focusPath?: string | undefined; onFeedback?: ((text: string) => Promise<void>) | undefined; disabled: boolean;
}) {
  const actions = useArtifacts();
  const [older, setOlder] = useState<Item[]>([]);
  const [cursor, setCursor] = useState(previousItemId);
  const [loading, setLoading] = useState(false);
  const currentItems = [...new Map([...older, ...items].map((item) => [item.id, item])).values()].filter((item) => entireTask || item.runId === runId).sort((a, b) => a.createdAt - b.createdAt || a.ordinal - b.ordinal);
  const changes = currentItems.filter((item) => item.toolCall?.changeDiff);
  const matchesFocus = (file: { oldPath: string; newPath: string }): boolean => (
    !focusPath || file.oldPath === focusPath || file.newPath === focusPath
  );
  const hasFocusedChange = !focusPath || changes.some((item) => {
    try { return parseDiff(item.toolCall!.changeDiff!).some(matchesFocus); } catch { return false; }
  });
  const [anchor, setAnchor] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function loadOlder() {
    if (!cursor) return;
    setLoading(true); setError("");
    try {
      const page = await window.eidosRuntime.readSession(sessionId, { beforeItemId: cursor, itemLimit: 200 });
      setOlder((current) => [...page.items, ...current]);
      setCursor(page.items.some((item) => item.runId !== runId) ? undefined : page.previousItemId);
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setLoading(false); }
  }
  async function send() {
    if (!onFeedback) return;
    setBusy(true); setError("");
    try { await onFeedback(`请处理以下文本修改反馈。以下位置属于工具执行时的 Diff，请先核对当前文件。\n${anchor}\n用户意见：${body}`); setBody(""); setAnchor(""); }
    catch (cause) { setError(userFacingError(cause)); }
    finally { setBusy(false); }
  }
  return <section className="last-turn-changes" aria-label={entireTask ? "整个任务修改记录" : "最近一轮修改"}>
    <p>这里按工具执行顺序展示文件修改记录。准备中的补丁不代表已经写入，失败的工具可能只完成部分写入。只有路径而没有补丁的记录无法展示历史差异。</p>
    {cursor && <button disabled={loading} onClick={() => void loadOlder()}>加载本轮更早的记录</button>}
    {error && <p role="alert">{error}</p>}
    {changes.map((item) => {
      const call = item.toolCall!;
      try {
        const files = parseDiff(call.changeDiff!).filter(matchesFocus);
        if (!files.length) return null;
        return <article key={item.id}>{entireTask && <p>轮次：{item.runId}</p>}<strong>{call.status === "running" ? "准备或执行中" : call.status === "completed" ? "执行完成" : "未完整完成"}</strong>
          {files.map((file) => <div key={`${file.oldPath}:${file.newPath}`}><button title="打开当前文件，历史内容以此处 Diff 为准" onClick={() => actions?.openFile(file.newPath === "/dev/null" ? file.oldPath : file.newPath)}>{file.newPath === "/dev/null" ? file.oldPath : file.newPath}</button>
            <Diff viewType="unified" diffType={file.type} hunks={file.hunks} gutterEvents={{ onClick: ({ change, side }) => {
              if (!change) return;
              const line = change.type === "normal" ? side === "old" ? change.oldLineNumber : change.newLineNumber : change.lineNumber;
              setAnchor(`Run: ${item.runId}\nToolCall: ${call.id}\n文件: ${side === "old" ? file.oldPath : file.newPath}\n位置: ${side ?? "new"} 第 ${line} 行\nBase SHA: ${call.baseSha256 ?? "无"}`);
            } }}>{(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}</Diff>
          </div>)}
        </article>;
      } catch { return <pre key={item.id}>{call.changeDiff}</pre>; }
    })}
    {!changes.length && <p>本轮还没有已记录的文件补丁。</p>}
    {focusPath && changes.length > 0 && !hasFocusedChange && <p>本轮未找到该文件的历史 Diff。</p>}
    {anchor && <div><pre>{anchor}</pre><textarea aria-label="本轮修改反馈" value={body} maxLength={8192} onChange={(event) => setBody(event.target.value)} /><button disabled={disabled || busy || !body.trim()} onClick={() => void send()}>发送反馈</button>{error && <p role="alert">{error}</p>}</div>}
  </section>;
}
