import { useMemo, useState } from "react";
import { parseDiff } from "react-diff-view";
import type { Item } from "../contracts.js";
import { LastTurnChanges } from "./LastTurnChanges.js";
import { WorkspaceFileIcon } from "./WorkspaceFileIcon.js";

export function TextReviewPanel({ sessionId, runId, path, items, loading, error, onFeedback, disabled }: {
  sessionId: string;
  runId: string;
  path?: string | undefined;
  items: Item[];
  loading: boolean;
  error?: string | undefined;
  onFeedback(text: string): Promise<void>;
  disabled: boolean;
}) {
  const [scope, setScope] = useState<"turn" | "task">("turn");
  const [focusPath, setFocusPath] = useState(path);

  const scopedItems = useMemo(() => {
    if (scope === "turn") {
      const turnItems = items.filter((item) => item.runId === runId);
      return turnItems.length > 0 ? turnItems : items;
    }
    return items;
  }, [items, scope, runId]);

  const fileSummaries = useMemo(() => {
    const fileMap = new Map<string, { additions: number; deletions: number }>();
    for (const item of scopedItems) {
      if (!item.toolCall?.changeDiff) continue;
      try {
        const parsed = parseDiff(item.toolCall.changeDiff);
        for (const file of parsed) {
          const raw = file.newPath && file.newPath !== "/dev/null" ? file.newPath : file.oldPath;
          const clean = raw.replace(/^[ab]\//, "");
          if (!clean) continue;
          let additions = 0;
          let deletions = 0;
          for (const hunk of file.hunks) {
            for (const change of hunk.changes) {
              if (change.type === "insert") additions++;
              else if (change.type === "delete") deletions++;
            }
          }
          const prev = fileMap.get(clean) ?? { additions: 0, deletions: 0 };
          fileMap.set(clean, {
            additions: prev.additions + additions,
            deletions: prev.deletions + deletions,
          });
        }
      } catch {
        // ignore parse error
      }
    }
    return Array.from(fileMap.entries())
      .map(([filePath, stats]) => ({ path: filePath, ...stats }))
      .sort((a, b) => a.path.localeCompare(b.path));
  }, [scopedItems]);

  const totalStats = useMemo(() => {
    let additions = 0;
    let deletions = 0;
    for (const file of fileSummaries) {
      additions += file.additions;
      deletions += file.deletions;
    }
    return { additions, deletions };
  }, [fileSummaries]);

  return (
    <section className="text-review-panel" aria-label="文本修改审查">
      <header className="text-review-header">
        <div className="git-scope-tabs" role="tablist" aria-label="审查范围">
          <button
            type="button"
            role="tab"
            aria-selected={scope === "turn"}
            onClick={() => { setScope("turn"); setFocusPath(undefined); }}
          >
            最近一轮修改
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={scope === "task"}
            onClick={() => { setScope("task"); setFocusPath(undefined); }}
          >
            整个任务修改
          </button>
        </div>

        {fileSummaries.length > 0 && (
          <div className="text-review-summary-bar">
            <span className="text-review-files-count">
              共 {fileSummaries.length} 个文件修改
            </span>
            <span className="turn-result__stats">
              {totalStats.additions > 0 && <ins>+{totalStats.additions}</ins>}
              {totalStats.deletions > 0 && <del>-{totalStats.deletions}</del>}
            </span>
          </div>
        )}
      </header>

      {fileSummaries.length > 1 && (
        <div className="text-review-chips-bar" role="navigation" aria-label="文件过滤">
          <button
            type="button"
            className={`text-review-chip${!focusPath ? " text-review-chip--active" : ""}`}
            onClick={() => setFocusPath(undefined)}
          >
            全部文件 ({fileSummaries.length})
          </button>
          {fileSummaries.map((file) => {
            const active = focusPath === file.path;
            const name = file.path.split("/").pop() || file.path;
            return (
              <button
                type="button"
                key={file.path}
                className={`text-review-chip${active ? " text-review-chip--active" : ""}`}
                title={file.path}
                onClick={() => setFocusPath(active ? undefined : file.path)}
              >
                <WorkspaceFileIcon name={file.path} />
                <span>{name}</span>
                <span className="turn-result__stats">
                  {file.additions > 0 && <ins>+{file.additions}</ins>}
                  {file.deletions > 0 && <del>-{file.deletions}</del>}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {loading && <p className="text-review-status" role="status">正在读取更早的修改记录…</p>}
      {error && <p className="text-review-error" role="alert">修改记录尚未完整读取：{error}</p>}

      <LastTurnChanges
        key={`${scope}:${focusPath ?? "all"}`}
        sessionId={sessionId}
        items={items}
        runId={runId}
        focusPath={focusPath}
        entireTask={scope === "task"}
        onFeedback={onFeedback}
        disabled={disabled}
      />
    </section>
  );
}
