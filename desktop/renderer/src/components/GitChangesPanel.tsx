import type {
  GitDiffScope,
  SessionGitDiff,
  SessionGitStatus,
} from "../contracts.js";
import { Button } from "./Button.js";


interface GitChangesPanelProps {
  scope: GitDiffScope;
  status: SessionGitStatus | undefined;
  diff: SessionGitDiff | undefined;
  loading: boolean;
  error: string | undefined;
  onScopeChange(scope: GitDiffScope): void;
  onRefresh(): void;
}

export function GitChangesPanel({
  scope,
  status,
  diff,
  loading,
  error,
  onScopeChange,
  onRefresh,
}: GitChangesPanelProps) {
  return (
    <section className="git-changes-panel" aria-label="Git Changes">
      <header className="git-changes-toolbar">
        <div className="git-scope-tabs" role="tablist" aria-label="Diff 范围">
          <button
            type="button"
            role="tab"
            aria-selected={scope === "head"}
            className="git-scope-tab"
            onClick={() => onScopeChange("head")}
          >
            未提交改动
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={scope === "baseline"}
            className="git-scope-tab"
            onClick={() => onScopeChange("baseline")}
          >
            整个任务改动
          </button>
        </div>
        <Button
          variant="ghost"
          size="small"
          loading={loading}
          aria-label="刷新 Git 变更"
          onClick={onRefresh}
        >
          刷新
        </Button>
      </header>

      {status && (
        <div className="git-review-summary" aria-label="Git 状态">
          <span className="git-review-branch">{status.branch}</span>
          <code>{status.head.slice(0, 7)}</code>
          <span>{status.dirty ? "有改动" : "干净"}</span>
        </div>
      )}
      {error && <p className="approval-error git-review-error" role="alert">{error}</p>}

      <div className="git-changes-body">
        <aside className="git-file-list" aria-label="Changed Files">
          <h2>Changed Files</h2>
          {diff?.changedFiles.length ? (
            <ul>
              {diff.changedFiles.map((path) => <li key={path}>{path}</li>)}
            </ul>
          ) : (
            <p>{loading ? "正在读取…" : "没有变更"}</p>
          )}
        </aside>
        <div className="git-diff-view">
          {diff?.truncated && (
            <p className="git-diff-truncated" role="status">Diff 已截断</p>
          )}
          {diff?.unifiedDiff ? (
            <pre tabIndex={0}>{diff.unifiedDiff}</pre>
          ) : (
            <p className="git-diff-empty">{loading ? "正在读取 Diff…" : "当前范围没有 Diff"}</p>
          )}
        </div>
      </div>
    </section>
  );
}
