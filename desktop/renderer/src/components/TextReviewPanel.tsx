import { useMemo, useState } from "react";
import { parseDiff } from "react-diff-view";
import type { Item } from "../contracts.js";
import { LastTurnChanges } from "./LastTurnChanges.js";

export function TextReviewPanel({ sessionId, runId, path, items, loading, error, onFeedback, disabled }: {
  sessionId: string; runId: string; path?: string | undefined; items: Item[];
  loading: boolean; error?: string | undefined; onFeedback(text: string): Promise<void>; disabled: boolean;
}) {
  const [scope, setScope] = useState<"turn" | "task">("turn");
  const [focusPath, setFocusPath] = useState(path);
  const pagedItems = items.filter((item) => item.toolCall?.changeDiffHash);
  const taskPaths = useMemo(() => [...new Set(items.flatMap((item) => {
    try { return parseDiff(item.toolCall?.changeDiff ?? "").map((file) => file.newPath === "/dev/null" ? file.oldPath : file.newPath); }
    catch { return []; }
  }))].filter(Boolean).sort(), [items]);
  return <section className="text-review-panel" aria-label="文本修改审查">
    <div className="git-scope-tabs" role="tablist" aria-label="审查范围">
      <button role="tab" aria-selected={scope === "turn"} onClick={() => setScope("turn")}>最近一轮修改</button>
      <button role="tab" aria-selected={scope === "task"} onClick={() => setScope("task")}>整个任务修改</button>
    </div>
    {loading && <p role="status">正在读取更早的修改记录…</p>}
    {error && <p role="alert">修改记录尚未完整读取：{error}</p>}
    {focusPath && <button onClick={() => setFocusPath(undefined)}>显示全部文件</button>}
    {scope === "turn" ? <LastTurnChanges key={focusPath ?? "all"} sessionId={sessionId} items={items}
      runId={runId} focusPath={focusPath} onFeedback={onFeedback} disabled={disabled} />
      : <>{taskPaths.filter((file) => !focusPath || file === focusPath).map((file) => <details key={file} open={Boolean(focusPath)}>
        <summary>{file}</summary>
        <LastTurnChanges sessionId={sessionId} items={items.filter((item) => !item.toolCall?.changeDiffHash)} entireTask focusPath={file} onFeedback={onFeedback} disabled={disabled} />
      </details>)}
      {pagedItems.length > 0 && <LastTurnChanges sessionId={sessionId} items={pagedItems} entireTask focusPath={focusPath} onFeedback={onFeedback} disabled={disabled} />}
      {!taskPaths.length && !pagedItems.length && <p>整个任务尚无可展示的文本补丁。</p>}</>}

  </section>;
}
