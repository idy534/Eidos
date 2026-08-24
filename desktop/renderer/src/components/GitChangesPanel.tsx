import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Diff,
  Hunk,
  getChangeKey,
  parseDiff,
  type ChangeData,
} from "react-diff-view";

import type {
  GitDiffScope,
  SessionGitDiff,
  SessionGitStatus,
  ReviewComment,
  ReviewCommentCreateInput,
} from "../contracts.js";
import { userFacingError } from "../session-state.js";
import { Button } from "./Button.js";
import { GitWorkflowControls } from "./GitWorkflowControls.js";


type ReviewGroup = "baseline" | "staged" | "changes" | "untracked" | "conflicts";

interface FileSelection {
  group: ReviewGroup;
  path: string;
}

interface FileGroup {
  id: ReviewGroup;
  label: string;
  paths: readonly string[];
}

interface ReviewFileState {
  diff?: SessionGitDiff;
  comments: ReviewComment[];
  loading: boolean;
  error?: string;
}

interface GitChangesPanelProps {
  sessionId: string;
  workspaceRoot: string;
  scope: GitDiffScope;
  status: SessionGitStatus | undefined;
  summary?: SessionGitDiff | undefined;
  loading: boolean;
  error: string | undefined;
  onScopeChange(scope: GitDiffScope): void;
  onRefresh(): void;
  readDiff?: (sessionId: string, scope: GitDiffScope, path?: string) => Promise<SessionGitDiff>;
  stage?: (sessionId: string, paths: string[], operationId: string) => Promise<unknown>;
  unstage?: (sessionId: string, paths: string[], operationId: string) => Promise<unknown>;
  discard?: (sessionId: string, path: string, operationId: string) => Promise<unknown>;
  openInEditor?: (sessionId: string, path: string) => Promise<void>;
  listComments?: (
    sessionId: string,
    path?: string,
    scope?: GitDiffScope,
  ) => Promise<ReviewComment[]>;
  createComment?: (
    sessionId: string,
    input: ReviewCommentCreateInput,
    operationId: string,
  ) => Promise<ReviewComment>;
  deleteComment?: (
    sessionId: string,
    commentId: string,
    operationId: string,
  ) => Promise<string>;
  onSendReviewFeedback?: (feedback: string) => Promise<void>;
  reviewFeedbackDisabled?: boolean;
  workflowDisabled?: boolean;
  workflowOpenRequest?: number | undefined;
  onCreateBranch?: (() => void) | undefined;
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
const defaultListComments: NonNullable<GitChangesPanelProps["listComments"]> = (
  id, path, scope,
) => window.eidosRuntime.listReviewComments(id, path, scope);
const defaultCreateComment: NonNullable<GitChangesPanelProps["createComment"]> = (
  id, input, operationId,
) => window.eidosRuntime.createReviewComment(id, input, operationId);
const defaultDeleteComment: NonNullable<GitChangesPanelProps["deleteComment"]> = (
  id, commentId, operationId,
) => window.eidosRuntime.deleteReviewComment(id, commentId, operationId);

interface CommentAnchor {
  side: "old" | "new";
  line: number;
}

export function formatReviewFeedback(comments: readonly ReviewComment[]): string {
  const active = comments.filter((comment) => comment.status === "active");
  return [
    "Please address the following review feedback:",
    ...active.map((comment) => (
      `- ${comment.path} (${comment.side} line ${comment.line}): ${comment.body}`
    )),
  ].join("\n");
}

function selectionKey(selection: FileSelection): string {
  return `${selection.group}:${selection.path}`;
}

function diffStats(diff: SessionGitDiff | undefined): { additions: number; deletions: number } {
  return { additions: diff?.additions ?? 0, deletions: diff?.deletions ?? 0 };
}

function ExpandIcon({ collapse }: { collapse: boolean }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      {collapse
        ? <path d="M5 3v5m-2-2 2 2 2-2M5 17v-5m-2 2 2-2 2 2M10 5h7M10 10h7M10 15h7" />
        : <path d="M5 8V3m-2 2 2-2 2 2M5 12v5m-2-2 2 2 2-2M10 5h7M10 10h7M10 15h7" />}
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M16.5 8a6.5 6.5 0 0 0-11.1-2L4 7.5M3.5 4.5v3h3M3.5 12a6.5 6.5 0 0 0 11.1 2L16 12.5M16.5 15.5v-3h-3" />
    </svg>
  );
}

function FileDisclosureIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className="git-file-disclosure-icon"
      data-state={expanded ? "open" : "closed"}
      aria-hidden="true"
    >
      <path d="m7 4 5 6-5 6" />
    </svg>
  );
}

export function GitChangesPanel(props: GitChangesPanelProps) {
  const summaryControlled = Object.prototype.hasOwnProperty.call(props, "summary");
  const {
    sessionId,
    workspaceRoot,
    scope,
    status,
    summary,
    loading,
    error,
    onScopeChange,
    onRefresh,
    readDiff = defaultReadDiff,
    stage = defaultStage,
    unstage = defaultUnstage,
    discard = defaultDiscard,
    openInEditor = defaultOpenInEditor,
    listComments = defaultListComments,
    createComment = defaultCreateComment,
    deleteComment = defaultDeleteComment,
    onSendReviewFeedback,
    reviewFeedbackDisabled = false,
    workflowDisabled = false,
    workflowOpenRequest,
    onCreateBranch,
  } = props;
  const [loadedSummary, setLoadedSummary] = useState<SessionGitDiff>();
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string>();
  const effectiveSummary = summary ?? loadedSummary;

  useEffect(() => {
    let current = true;
    setLoadedSummary(undefined);
    setSummaryError(undefined);
    if (summaryControlled) {
      setSummaryLoading(false);
      return () => { current = false; };
    }
    setSummaryLoading(true);
    void readDiff(sessionId, scope).then((nextSummary) => {
      if (current) setLoadedSummary(nextSummary);
    }).catch((cause: unknown) => {
      if (current) setSummaryError(userFacingError(cause));
    }).finally(() => {
      if (current) setSummaryLoading(false);
    });
    return () => { current = false; };
  }, [readDiff, scope, sessionId, summary, summaryControlled]);

  const groups = useMemo<readonly FileGroup[]>(() => scope === "baseline"
    ? [{ id: "baseline", label: "整个任务", paths: effectiveSummary?.changedFiles ?? [] }]
    : [
        { id: "staged", label: "已暂存", paths: status?.stagedFiles ?? [] },
        { id: "changes", label: "修改", paths: status?.unstagedFiles ?? [] },
        { id: "untracked", label: "未跟踪", paths: status?.untrackedFiles ?? [] },
        { id: "conflicts", label: "冲突", paths: status?.conflictFiles ?? [] },
      ], [effectiveSummary?.changedFiles, scope, status]);
  const visibleGroups = useMemo(
    () => groups.filter((group) => group.paths.length > 0),
    [groups],
  );
  const selections = useMemo(
    () => groups.flatMap((group) => group.paths.map((path) => ({ group: group.id, path }))),
    [groups],
  );
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());
  const [fileStates, setFileStates] = useState<Record<string, ReviewFileState>>({});
  const [actionLoading, setActionLoading] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>(undefined);
  const [draft, setDraft] = useState<{ key: string; anchor: CommentAnchor }>();
  const [draftBody, setDraftBody] = useState("");
  const [commentLoading, setCommentLoading] = useState(false);
  const requestVersion = useRef(0);
  const loadingKeys = useRef(new Set<string>());
  const loadedKeys = useRef(new Set<string>());

  useEffect(() => {
    requestVersion.current += 1;
    loadingKeys.current.clear();
    loadedKeys.current.clear();
    setExpandedKeys(new Set());
    setFileStates({});
    setLocalError(undefined);
    setDraft(undefined);
    setDraftBody("");
  }, [scope, sessionId]);

  const loadFile = useCallback((selection: FileSelection): Promise<void> => {
    const key = selectionKey(selection);
    if (loadingKeys.current.has(key) || loadedKeys.current.has(key)) return Promise.resolve();
    const version = requestVersion.current;
    loadingKeys.current.add(key);
    setFileStates((current) => ({
      ...current,
      [key]: { comments: current[key]?.comments ?? [], loading: true },
    }));
    return Promise.allSettled([
      readDiff(sessionId, scope, selection.path),
      listComments(sessionId, selection.path, scope),
    ]).then(([diffResult, commentsResult]) => {
      if (requestVersion.current !== version) return;
      if (diffResult.status === "fulfilled") loadedKeys.current.add(key);
      const nextError = diffResult.status === "rejected"
        ? userFacingError(diffResult.reason)
        : commentsResult.status === "rejected"
          ? userFacingError(commentsResult.reason)
          : undefined;
      setFileStates((current) => ({
        ...current,
        [key]: {
          ...(diffResult.status === "fulfilled" ? { diff: diffResult.value } : {}),
          comments: commentsResult.status === "fulfilled" ? commentsResult.value : [],
          loading: false,
          ...(nextError === undefined ? {} : { error: nextError }),
        },
      }));
    }).finally(() => loadingKeys.current.delete(key));
  }, [listComments, readDiff, scope, sessionId]);

  const toggleFile = (selection: FileSelection): void => {
    const key = selectionKey(selection);
    const expanding = !expandedKeys.has(key);
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    if (expanding) void loadFile(selection);
  };

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

  const submitComment = async (): Promise<void> => {
    const selection = selections.find((candidate) => selectionKey(candidate) === draft?.key);
    const fileDiff = draft ? fileStates[draft.key]?.diff : undefined;
    if (!selection || !fileDiff || !draft || !draftBody.trim()) return;
    setCommentLoading(true);
    setLocalError(undefined);
    try {
      const comment = await createComment(sessionId, {
        commentId: crypto.randomUUID(),
        path: selection.path,
        scope,
        side: draft.anchor.side,
        line: draft.anchor.line,
        body: draftBody.trim(),
        baseHead: fileDiff.head,
        diffHash: fileDiff.diffHash,
      }, operationId());
      setFileStates((current) => ({
        ...current,
        [draft.key]: {
          ...current[draft.key]!,
          comments: [...(current[draft.key]?.comments ?? []), comment],
        },
      }));
      setDraft(undefined);
      setDraftBody("");
    } catch (cause: unknown) {
      setLocalError(userFacingError(cause));
    } finally {
      setCommentLoading(false);
    }
  };

  const removeComment = async (key: string, commentId: string): Promise<void> => {
    setCommentLoading(true);
    setLocalError(undefined);
    try {
      await deleteComment(sessionId, commentId, operationId());
      setFileStates((current) => ({
        ...current,
        [key]: {
          ...current[key]!,
          comments: (current[key]?.comments ?? []).filter((comment) => comment.id !== commentId),
        },
      }));
    } catch (cause: unknown) {
      setLocalError(userFacingError(cause));
    } finally {
      setCommentLoading(false);
    }
  };

  const sendReviewFeedback = async (): Promise<void> => {
    if (!onSendReviewFeedback) return;
    setCommentLoading(true);
    setLocalError(undefined);
    try {
      const allComments = await listComments(sessionId);
      const active = allComments.filter((comment) => comment.status === "active");
      if (!active.length) return;
      await onSendReviewFeedback(formatReviewFeedback(active));
    } catch (cause: unknown) {
      setLocalError(userFacingError(cause));
    } finally {
      setCommentLoading(false);
    }
  };

  const stats = diffStats(effectiveSummary);
  const allFilesExpanded = selections.length > 0
    && selections.every((selection) => expandedKeys.has(selectionKey(selection)));
  const compareRef = effectiveSummary?.compareRef
    ?? (scope === "baseline" ? status?.baseRef ?? effectiveSummary?.baseCommit?.slice(0, 7) : "HEAD");

  return (
    <section className="git-changes-panel" aria-label="Git Changes">
      {status && (
        <GitWorkflowControls
          sessionId={sessionId}
          workspaceRoot={workspaceRoot}
          status={status}
          disabled={workflowDisabled}
          openRequest={workflowOpenRequest}
          onRefresh={onRefresh}
          onCreateBranch={onCreateBranch}
        />
      )}
      <header className="git-changes-toolbar">
        <div className="git-scope-tabs" role="tablist" aria-label="Diff 范围">
          <button
            type="button"
            role="tab"
            aria-selected={scope === "head"}
            className="git-scope-tab"
            onClick={() => onScopeChange("head")}
          >
            未提交
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={scope === "baseline"}
            className="git-scope-tab"
            onClick={() => onScopeChange("baseline")}
          >
            整个任务
          </button>
        </div>
        <div className="git-review-toolbar-actions">
          <Button
            variant="ghost"
            size="small"
            className="git-icon-button"
            icon={<ExpandIcon collapse={allFilesExpanded} />}
            aria-label={allFilesExpanded ? "折叠全部差异" : "展开全部差异"}
            title={allFilesExpanded ? "折叠全部差异" : "展开全部差异"}
            disabled={selections.length === 0}
            onClick={() => {
              if (allFilesExpanded) {
                setExpandedKeys(new Set());
                return;
              }
              setExpandedKeys(new Set(selections.map(selectionKey)));
              void (async () => {
                for (const selection of selections) await loadFile(selection);
              })();
            }}
          >
            <span className="sr-only">{allFilesExpanded ? "折叠全部差异" : "展开全部差异"}</span>
          </Button>
          <Button
            variant="ghost"
            size="small"
            className="git-icon-button"
            icon={<RefreshIcon />}
            loading={loading || summaryLoading}
            aria-label="刷新 Git 变更"
            title="刷新 Git 变更"
            onClick={onRefresh}
          >
            <span className="sr-only">刷新 Git 变更</span>
          </Button>
          {onSendReviewFeedback && (
            <Button
              variant="secondary"
              size="small"
              className="git-review-feedback"
              disabled={commentLoading || reviewFeedbackDisabled}
              onClick={() => void sendReviewFeedback()}
            >
              发送审阅意见
            </Button>
          )}
        </div>
      </header>

      {status && (
        <div className="git-review-summary" aria-label="Git 状态">
          <div className="git-review-stats">
            <span className="git-review-stat git-review-stat--addition">+{stats.additions}</span>
            <span className="git-review-stat git-review-stat--deletion">-{stats.deletions}</span>
            {effectiveSummary?.statsIncomplete === true && (
              <span className="git-review-incomplete">统计不完整</span>
            )}
          </div>
          <div
            className="git-review-compare"
            title={`${status.branch ?? "Detached HEAD"} → ${compareRef ?? "未设置基线"}`}
          >
            <span className="git-review-branch">{status.branch ?? "Detached HEAD"}</span>
            <span aria-hidden="true">→</span>
            <code>{compareRef ?? "未设置基线"}</code>
          </div>
        </div>
      )}
      {(error || summaryError || localError) && (
        <p className="approval-error git-review-error" role="alert">
          {localError ?? summaryError ?? error}
        </p>
      )}

      <div className="git-review-files" aria-label="所有修改文件">
        {visibleGroups.map((group) => (
          <section className="git-file-group" key={group.id} aria-label={group.label}>
            <h2>{group.label} <span>{group.paths.length}</span></h2>
            {group.paths.map((path) => {
              const selection = { group: group.id, path } satisfies FileSelection;
              const key = selectionKey(selection);
              const expanded = expandedKeys.has(key);
              const state = fileStates[key];
              const summaryFileStats = effectiveSummary?.fileStats?.find((item) => item.path === path);
              const fileStats = summaryFileStats ?? diffStats(state?.diff);
              const hasFileStats = summaryFileStats !== undefined || state?.diff !== undefined;
              const parsedFiles = (() => {
                if (!state?.diff?.unifiedDiff) return [];
                try { return parseDiff(state.diff.unifiedDiff); } catch { return []; }
              })();
              const staleComments = (state?.comments ?? []).filter((comment) => (
                comment.status === "stale"
              ));
              const draftAnchor = draft?.key === key ? draft.anchor : undefined;
              return (
                <article className="git-review-file" key={key}>
                  <header className="git-review-file-header">
                    <button
                      type="button"
                      className="git-file-button"
                      aria-expanded={expanded}
                      aria-controls={`git-review-diff-${encodeURIComponent(key)}`}
                      onClick={() => toggleFile(selection)}
                    >
                      <span className="git-file-disclosure"><FileDisclosureIcon expanded={expanded} /></span>
                      <code>{path}</code>
                      {hasFileStats && (
                        <span className="git-file-stats">
                          <span>+{fileStats.additions}</span>
                          <span>-{fileStats.deletions}</span>
                          {(summaryFileStats?.statsIncomplete || state?.diff?.statsIncomplete) && (
                            <span>不完整</span>
                          )}
                        </span>
                      )}
                    </button>
                    {expanded && (
                      <div className="git-file-actions">
                        {selection.group === "staged" && (
                          <Button size="small" variant="secondary" disabled={actionLoading}
                            onClick={() => void runMutation(() => unstage(
                              sessionId, [path], operationId(),
                            ))}>
                            取消暂存
                          </Button>
                        )}
                        {(selection.group === "changes" || selection.group === "untracked") && (
                          <>
                            <Button size="small" variant="secondary" disabled={actionLoading}
                              onClick={() => void runMutation(() => stage(
                                sessionId, [path], operationId(),
                              ))}>
                              暂存
                            </Button>
                            <Button size="small" variant="danger" disabled={actionLoading}
                              onClick={() => {
                                if (window.confirm(`丢弃 ${path} 中的改动？`)) {
                                  void runMutation(() => discard(sessionId, path, operationId()));
                                }
                              }}>
                              丢弃
                            </Button>
                          </>
                        )}
                        <Button size="small" variant="ghost" disabled={actionLoading}
                          onClick={() => {
                            setLocalError(undefined);
                            void openInEditor(sessionId, path).catch((cause: unknown) => {
                              setLocalError(userFacingError(cause));
                            });
                          }}>
                          在编辑器中打开
                        </Button>
                      </div>
                    )}
                  </header>
                  {expanded && (
                    <div
                      id={`git-review-diff-${encodeURIComponent(key)}`}
                      className="git-file-diff-scroll"
                    >
                      {state?.error && <p className="approval-error" role="alert">{state.error}</p>}
                      {state?.diff?.truncated && (
                        <p className="git-diff-truncated" role="status">Diff 已截断</p>
                      )}
                      {parsedFiles.map((file) => (
                        <Diff
                          key={`${file.oldPath}:${file.newPath}`}
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
                              const anchor = change && commentAnchor(change, side);
                              if (anchor) {
                                setDraft({ key, anchor });
                                setDraftBody("");
                              }
                            },
                          }}
                          widgets={commentWidgets(
                            file.hunks.flatMap((hunk) => hunk.changes),
                            state?.comments ?? [],
                            draftAnchor,
                            <div className="review-comment-draft">
                              <textarea
                                aria-label="审阅评论"
                                value={draftBody}
                                onChange={(event) => setDraftBody(event.target.value)}
                                placeholder="添加审阅意见…"
                                maxLength={16_384}
                              />
                              <div>
                                <Button size="small" variant="primary" loading={commentLoading}
                                  disabled={!draftBody.trim()} onClick={() => void submitComment()}>
                                  添加评论
                                </Button>
                                <Button size="small" variant="ghost" onClick={() => {
                                  setDraft(undefined);
                                  setDraftBody("");
                                }}>
                                  取消
                                </Button>
                              </div>
                            </div>,
                            (commentId) => void removeComment(key, commentId),
                          )}
                        >
                          {(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}
                        </Diff>
                      ))}
                      {staleComments.length > 0 && (
                        <aside className="review-stale-comments" aria-label="过期审阅评论">
                          <strong>过期评论</strong>
                          {staleComments.map((comment) => (
                            <div key={comment.id}>
                              <span>{comment.side} 第 {comment.line} 行</span>
                              <p>{comment.body}</p>
                              <button type="button" onClick={() => void removeComment(key, comment.id)}>删除</button>
                            </div>
                          ))}
                        </aside>
                      )}
                      {!parsedFiles.length && !state?.error && (
                        <p className="git-diff-empty">
                          {state?.loading ? "正在读取 Diff…" : "该文件没有可显示的文本 Diff"}
                        </p>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        ))}
        {selections.length === 0 && (
          loading || summaryLoading ? (
            <p className="git-diff-empty" role="status">正在读取…</p>
          ) : (
            <div className="git-review-empty" role="status">
              <strong>当前范围没有变更</strong>
              <span>可以切换范围或刷新 Git 状态。</span>
            </div>
          )
        )}
      </div>
    </section>
  );
}

function commentAnchor(change: ChangeData, side?: "old" | "new"): CommentAnchor | undefined {
  if (change.type === "insert") return { side: "new", line: change.lineNumber };
  if (change.type === "delete") return { side: "old", line: change.lineNumber };
  if (side === "old") return { side, line: change.oldLineNumber };
  return { side: "new", line: change.newLineNumber };
}

function commentWidgets(
  changes: readonly ChangeData[],
  comments: readonly ReviewComment[],
  draftAnchor: CommentAnchor | undefined,
  draft: ReactNode,
  onDelete: (commentId: string) => void,
): Record<string, ReactNode> {
  const widgets: Record<string, ReactNode> = {};
  for (const change of changes) {
    const anchors = change.type === "normal"
      ? [commentAnchor(change, "old"), commentAnchor(change, "new")]
      : [commentAnchor(change)];
    const anchored = comments.filter((comment) => (
      comment.status === "active"
      && anchors.some((anchor) => anchor?.side === comment.side && anchor?.line === comment.line)
    ));
    const hasDraft = draftAnchor && anchors.some((anchor) => (
      anchor?.side === draftAnchor.side && anchor.line === draftAnchor.line
    ));
    if (!anchored.length && !hasDraft) continue;
    widgets[getChangeKey(change)] = (
      <div className="review-comment-widget">
        {anchored.map((comment) => (
          <div className="review-comment" key={comment.id}>
            <p>{comment.body}</p>
            <button type="button" onClick={() => onDelete(comment.id)}>删除</button>
          </div>
        ))}
        {hasDraft ? draft : null}
      </div>
    );
  }
  return widgets;
}
