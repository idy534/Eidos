import { useEffect, useMemo, useState } from "react";
import { Diff, Hunk, parseDiff } from "react-diff-view";

import type {
  GitDiffScope,
  SessionGitDiff,
  SessionGitStatus,
} from "../contracts.js";
import { userFacingError } from "../session-state.js";
import { Button } from "./Button.js";


type ReviewGroup = "staged" | "changes" | "untracked" | "conflicts";

interface FileSelection {
  group: ReviewGroup;
  path: string;
}

interface FileGroup {
  id: ReviewGroup;
  label: string;
  paths: readonly string[];
}

interface GitChangesPanelProps {
  sessionId: string;
  scope: GitDiffScope;
  status: SessionGitStatus | undefined;
  loading: boolean;
  error: string | undefined;
  onScopeChange(scope: GitDiffScope): void;
  onRefresh(): void;
  readDiff?: (sessionId: string, scope: GitDiffScope, path: string) => Promise<SessionGitDiff>;
  stage?: (sessionId: string, paths: string[], operationId: string) => Promise<unknown>;
  unstage?: (sessionId: string, paths: string[], operationId: string) => Promise<unknown>;
  discard?: (sessionId: string, path: string, operationId: string) => Promise<unknown>;
  openInEditor?: (sessionId: string, path: string) => Promise<void>;
}

const defaultReadDiff: NonNullable<GitChangesPanelProps["readDiff"]> = (id, scope, path) => (
  window.eidosRuntime.readSessionGitDiff(id, scope, path)
);
const defaultStage: NonNullable<GitChangesPanelProps["stage"]> = (id, paths, operationId) => (
  window.eidosRuntime.stageSessionGit(id, paths, operationId)
);
const defaultUnstage: NonNullable<GitChangesPanelProps["unstage"]> = (id, paths, operationId) => (
  window.eidosRuntime.unstageSessionGit(id, paths, operationId)
);
const defaultDiscard: NonNullable<GitChangesPanelProps["discard"]> = (id, path, operationId) => (
  window.eidosRuntime.discardSessionGit(id, path, operationId)
);
const defaultOpenInEditor: NonNullable<GitChangesPanelProps["openInEditor"]> = (id, path) => (
  window.eidosRuntime.openWorkspacePathInEditor(id, path)
);

function sameSelection(left: FileSelection | undefined, right: FileSelection): boolean {
  return left?.group === right.group && left.path === right.path;
}

export function GitChangesPanel({
  sessionId,
  scope,
  status,
  loading,
  error,
  onScopeChange,
  onRefresh,
  readDiff = defaultReadDiff,
  stage = defaultStage,
  unstage = defaultUnstage,
  discard = defaultDiscard,
  openInEditor = defaultOpenInEditor,
}: GitChangesPanelProps) {
  const groups = useMemo<readonly FileGroup[]>(() => [
    { id: "staged", label: "Staged", paths: status?.stagedFiles ?? [] },
    { id: "changes", label: "Changes", paths: status?.unstagedFiles ?? [] },
    { id: "untracked", label: "Untracked", paths: status?.untrackedFiles ?? [] },
    { id: "conflicts", label: "Conflicts", paths: status?.conflictFiles ?? [] },
  ], [status]);
  const selections = useMemo(
    () => groups.flatMap((group) => group.paths.map((path) => ({ group: group.id, path }))),
    [groups],
  );
  const [selection, setSelection] = useState<FileSelection | undefined>(undefined);
  const [fileDiff, setFileDiff] = useState<SessionGitDiff | undefined>(undefined);
  const [diffLoading, setDiffLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>(undefined);

  useEffect(() => {
    setSelection((current) => (
      current && selections.some((candidate) => sameSelection(current, candidate))
        ? current
        : selections[0]
    ));
  }, [selections]);

  useEffect(() => {
    let current = true;
    setFileDiff(undefined);
    setLocalError(undefined);
    if (!selection) return () => { current = false; };
    setDiffLoading(true);
    void readDiff(sessionId, scope, selection.path).then((nextDiff) => {
      if (current) setFileDiff(nextDiff);
    }).catch((cause: unknown) => {
      if (current) setLocalError(userFacingError(cause));
    }).finally(() => {
      if (current) setDiffLoading(false);
    });
    return () => { current = false; };
  }, [readDiff, scope, selection, sessionId]);

  const parsedFiles = useMemo(() => {
    if (!fileDiff?.unifiedDiff) return [];
    try {
      return parseDiff(fileDiff.unifiedDiff);
    } catch {
      return [];
    }
  }, [fileDiff]);

  const runMutation = async (operation: () => Promise<unknown>): Promise<void> => {
    setActionLoading(true);
    setLocalError(undefined);
    try {
      await operation();
      onRefresh();
    } catch (cause: unknown) {
      setLocalError(userFacingError(cause));
    } finally {
      setActionLoading(false);
    }
  };

  const operationId = (): string => crypto.randomUUID();
  const selectedPath = selection?.path;

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
      {(error || localError) && (
        <p className="approval-error git-review-error" role="alert">{localError ?? error}</p>
      )}

      <div className="git-changes-body">
        <aside className="git-file-list" aria-label="Changed Files">
          {groups.map((group) => (
            <section className="git-file-group" key={group.id} aria-label={group.label}>
              <h2>{group.label} <span>{group.paths.length}</span></h2>
              {group.paths.length > 0 && (
                <ul>
                  {group.paths.map((path) => (
                    <li key={path}>
                      <button
                        type="button"
                        className="git-file-button"
                        aria-pressed={sameSelection(selection, { group: group.id, path })}
                        onClick={() => setSelection({ group: group.id, path })}
                      >
                        {path}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))}
          {selections.length === 0 && <p>{loading ? "正在读取…" : "没有变更"}</p>}
        </aside>

        <div className="git-diff-view">
          {selection && (
            <header className="git-file-actions">
              <code title={selection.path}>{selection.path}</code>
              <div>
                {selection.group === "staged" && (
                  <Button
                    size="small"
                    variant="secondary"
                    disabled={actionLoading}
                    onClick={() => void runMutation(() => unstage(
                      sessionId, [selection.path], operationId(),
                    ))}
                  >
                    Unstage
                  </Button>
                )}
                {(selection.group === "changes" || selection.group === "untracked") && (
                  <>
                    <Button
                      size="small"
                      variant="secondary"
                      disabled={actionLoading}
                      onClick={() => void runMutation(() => stage(
                        sessionId, [selection.path], operationId(),
                      ))}
                    >
                      Accept
                    </Button>
                    <Button
                      size="small"
                      variant="danger"
                      disabled={actionLoading}
                      onClick={() => {
                        if (window.confirm(`Discard changes in ${selection.path}?`)) {
                          void runMutation(() => discard(
                            sessionId, selection.path, operationId(),
                          ));
                        }
                      }}
                    >
                      Discard
                    </Button>
                  </>
                )}
                <Button
                  size="small"
                  variant="ghost"
                  disabled={actionLoading}
                  onClick={() => {
                    setLocalError(undefined);
                    void openInEditor(sessionId, selection.path).catch((cause: unknown) => {
                      setLocalError(userFacingError(cause));
                    });
                  }}
                >
                  Open in Editor
                </Button>
              </div>
            </header>
          )}
          <div className="git-file-diff-scroll">
            {fileDiff?.truncated && (
              <p className="git-diff-truncated" role="status">Diff 已截断</p>
            )}
            {parsedFiles.map((file) => (
              <Diff key={`${file.oldPath}:${file.newPath}`} viewType="unified" diffType={file.type} hunks={file.hunks}>
                {(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}
              </Diff>
            ))}
            {!parsedFiles.length && (
              <p className="git-diff-empty">
                {diffLoading ? "正在读取 Diff…" : selectedPath ? "该文件没有可显示的文本 Diff" : "请选择文件"}
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
