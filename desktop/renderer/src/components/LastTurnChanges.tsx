import { useState } from "react";
import { Diff, Hunk, parseDiff } from "react-diff-view";
import type { Item } from "../contracts.js";
import { useArtifacts } from "./ArtifactContext.js";
import { userFacingError } from "../session-state.js";
import { ToolTextView } from "./ToolTextView.js";
import { Button } from "./Button.js";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";

function fileName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function cleanFilePath(file: { oldPath: string; newPath: string }): string {
  const raw = file.newPath && file.newPath !== "/dev/null" ? file.newPath : file.oldPath;
  return raw.replace(/^[ab]\//, "");
}

function matchesFocus(file: { oldPath: string; newPath: string }, focus?: string): boolean {
  if (!focus) return true;
  const clean = cleanFilePath(file);
  const cleanFocus = focus.replace(/^[ab]\//, "");
  return clean === cleanFocus || file.oldPath === focus || file.newPath === focus;
}

function computeDiffStats(hunks: Array<{ changes: Array<{ type: string }> }>): { additions: number; deletions: number } {
  let additions = 0;
  let deletions = 0;
  for (const hunk of hunks) {
    for (const change of hunk.changes) {
      if (change.type === "insert") additions++;
      else if (change.type === "delete") deletions++;
    }
  }
  return { additions, deletions };
}

export function LastTurnChanges({ sessionId, previousItemId, items, runId, focusPath, onFeedback, disabled, entireTask = false }: {
  entireTask?: boolean;
  sessionId: string;
  previousItemId?: string | undefined;
  items: Item[];
  runId?: string | undefined;
  focusPath?: string | undefined;
  onFeedback?: ((text: string) => Promise<void>) | undefined;
  disabled: boolean;
}) {
  const actions = useArtifacts();
  const [older, setOlder] = useState<Item[]>([]);
  const [cursor, setCursor] = useState(previousItemId);
  const [loading, setLoading] = useState(false);
  const currentItems = [...new Map([...older, ...items].map((item) => [item.id, item])).values()]
    .filter((item) => entireTask || item.runId === runId)
    .sort((a, b) => a.createdAt - b.createdAt || a.ordinal - b.ordinal);
  const changes = currentItems.filter((item) => item.toolCall?.changeDiff || item.toolCall?.changeDiffHash);
  const hasFocusedChange = !focusPath || changes.some((item) => {
    if (item.toolCall?.changeDiffHash) return true;
    try { return parseDiff(item.toolCall!.changeDiff!).some((f) => matchesFocus(f, focusPath)); } catch { return false; }
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
    try {
      await onFeedback(`请处理以下文本修改反馈。以下位置属于工具执行时的 Diff，请先核对当前文件。\n${anchor}\n用户意见：${body}`);
      setBody("");
      setAnchor("");
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setBusy(false); }
  }

  return (
    <section className="last-turn-changes" aria-label={entireTask ? "整个任务修改记录" : "最近一轮修改"}>
      {cursor && (
        <div className="text-review-load-older">
          <Button variant="secondary" size="small" disabled={loading} onClick={() => void loadOlder()}>
            加载更早的修改记录
          </Button>
        </div>
      )}
      {error && <p className="text-review-error" role="alert">{error}</p>}
      <div className="text-review-files-list">
        {changes.map((item) => {
          const call = item.toolCall!;
          if (call.changeDiffHash) {
            return (
              <article className="text-review-card" key={item.id}>
                {focusPath && <p className="text-review-note">完整补丁包含此次工具调用的全部文件。</p>}
                <ToolTextView key={call.changeDiffHash} sessionId={sessionId} toolCallId={call.id} field="diff" sha256={call.changeDiffHash} totalBytes={call.changeDiffBytes!} />
              </article>
            );
          }
          try {
            const files = parseDiff(call.changeDiff!).filter((f) => matchesFocus(f, focusPath));
            if (!files.length) return null;
            return files.map((file) => {
              const cleanPath = cleanFilePath(file);
              const fileNameStr = fileName(cleanPath);
              const dirStr = cleanPath.slice(0, Math.max(0, cleanPath.length - fileNameStr.length));
              const stats = computeDiffStats(file.hunks);
              return (
                <article className="text-review-card" key={`${item.id}:${file.oldPath}:${file.newPath}`}>
                  <header className="text-review-file-header">
                    <div className="text-review-file-info">
                      <span className="text-review-file-icon" aria-hidden="true">
                        <WorkspaceFileIcon name={cleanPath} />
                      </span>
                      <button
                        type="button"
                        className="text-review-file-path-btn"
                        title={`在工作区打开 ${cleanPath}`}
                        aria-label={cleanPath}
                        onClick={() => actions?.openFile(cleanPath)}
                      >
                        {dirStr && <span className="text-review-file-dir">{dirStr}</span>}
                        <strong>{fileNameStr}</strong>
                      </button>
                      <span className="turn-result__stats">
                        {stats.additions > 0 && <ins>+{stats.additions}</ins>}
                        {stats.deletions > 0 && <del>-{stats.deletions}</del>}
                        {stats.additions === 0 && stats.deletions === 0 && <span className="turn-result__stats--unknown">无增减</span>}
                      </span>
                      {call.status && call.status !== "completed" && (
                        <span className="text-review-status-badge">
                          {call.status === "running" ? "执行中" : "未完成"}
                        </span>
                      )}
                    </div>
                    <div className="text-review-file-actions">
                      <Button
                        variant="ghost"
                        size="small"
                        className="text-review-open-btn"
                        title={`在工作区打开 ${cleanPath}`}
                        onClick={() => actions?.openFile(cleanPath)}
                      >
                        打开文件
                      </Button>
                    </div>
                  </header>
                  <div className="git-file-diff-scroll">
                    <Diff
                      className="git-diff-unified"
                      viewType="unified"
                      diffType={file.type}
                      hunks={file.hunks}
                      gutterClassName="git-diff-line-numbers"
                      renderGutter={({ change, side, renderDefault }) => {
                        if (side === "new") return null;
                        return change.type === "insert" ? change.lineNumber : renderDefault();
                      }}
                      gutterEvents={{
                        onClick: ({ change, side }) => {
                          if (!change) return;
                          const line = change.type === "normal"
                            ? (side === "old" ? change.oldLineNumber : change.newLineNumber)
                            : change.lineNumber;
                          setAnchor(`Run: ${item.runId}\nToolCall: ${call.id}\n文件: ${cleanPath}\n位置: ${side ?? "new"} 第 ${line} 行\nBase SHA: ${call.baseSha256 ?? "无"}`);
                        }
                      }}
                    >
                      {(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}
                    </Diff>
                  </div>
                </article>
              );
            });
          } catch {
            return (
              <article className="text-review-card" key={item.id}>
                <pre className="text-review-raw-diff">{call.changeDiff}</pre>
              </article>
            );
          }
        })}
      </div>
      {!changes.length && (
        <div className="text-review-empty">
          <p>{entireTask ? "整个任务尚无可展示的文本补丁。" : "本轮还没有已记录的文件补丁。"}</p>
        </div>
      )}
      {focusPath && changes.length > 0 && !hasFocusedChange && (
        <div className="text-review-empty">
          <p>未找到该文件的历史 Diff。</p>
        </div>
      )}
      {anchor && (
        <div className="text-review-feedback-box" role="region" aria-label="代码审阅反馈">
          <div className="text-review-feedback-header">
            <span className="text-review-feedback-title">针对所选代码行的修改意见</span>
            <button
              type="button"
              className="text-review-feedback-close"
              aria-label="取消反馈"
              title="取消反馈"
              onClick={() => { setAnchor(""); setBody(""); }}
            >
              ×
            </button>
          </div>
          <pre className="text-review-feedback-anchor">{anchor}</pre>
          <textarea
            className="text-review-feedback-input"
            aria-label="本轮修改反馈"
            placeholder="输入针对选中代码行的修改意见..."
            value={body}
            maxLength={8192}
            onChange={(event) => setBody(event.target.value)}
          />
          <div className="text-review-feedback-actions">
            <Button
              variant="ghost"
              size="small"
              onClick={() => { setAnchor(""); setBody(""); }}
            >
              取消
            </Button>
            <Button
              variant="secondary"
              size="small"
              disabled={disabled || busy || !body.trim()}
              loading={busy}
              onClick={() => void send()}
            >
              发送反馈
            </Button>
          </div>
          {error && <p className="text-review-error" role="alert">{error}</p>}
        </div>
      )}
    </section>
  );
}
