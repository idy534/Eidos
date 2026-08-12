import { useEffect, useMemo, useState, type ReactNode } from "react";
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
  workspaceRoot: string;
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

function sameSelection(left: FileSelection | undefined, right: FileSelection): boolean {
  return left?.group === right.group && left.path === right.path;
}

export function GitChangesPanel({
  sessionId,
  workspaceRoot,
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
  listComments = defaultListComments,
  createComment = defaultCreateComment,
  deleteComment = defaultDeleteComment,
  onSendReviewFeedback,
  reviewFeedbackDisabled = false,
  workflowDisabled = false,
  onCreateBranch,
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
  const [comments, setComments] = useState<ReviewComment[]>([]);
  const [draftAnchor, setDraftAnchor] = useState<CommentAnchor | undefined>();
  const [draftBody, setDraftBody] = useState("");
  const [commentLoading, setCommentLoading] = useState(false);

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

  useEffect(() => {
    let current = true;
    setComments([]);
    setDraftAnchor(undefined);
    setDraftBody("");
    if (!selection) return () => { current = false; };
    void listComments(sessionId, selection.path, scope).then((nextComments) => {
      if (current) setComments(nextComments);
    }).catch((cause: unknown) => {
      if (current) setLocalError(userFacingError(cause));
    });
    return () => { current = false; };
  }, [listComments, scope, selection, sessionId]);

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
  const staleComments = comments.filter((comment) => comment.status === "stale");

  const submitComment = async (): Promise<void> => {
    if (!selection || !fileDiff || !draftAnchor || !draftBody.trim()) return;
    setCommentLoading(true);
    setLocalError(undefined);
    try {
      const comment = await createComment(sessionId, {
        commentId: crypto.randomUUID(),
        path: selection.path,
        scope,
        side: draftAnchor.side,
        line: draftAnchor.line,
        body: draftBody.trim(),
        baseHead: fileDiff.head,
        diffHash: fileDiff.diffHash,
      }, operationId());
      setComments((current) => [...current, comment]);
      setDraftAnchor(undefined);
      setDraftBody("");
    } catch (cause: unknown) {
      setLocalError(userFacingError(cause));
    } finally {
      setCommentLoading(false);
    }
  };

  const removeComment = async (commentId: string): Promise<void> => {
    setCommentLoading(true);
    setLocalError(undefined);
    try {
      await deleteComment(sessionId, commentId, operationId());
      setComments((current) => current.filter((comment) => comment.id !== commentId));
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

  return (
    <section className="git-changes-panel" aria-label="Git Changes">
      {status && (
        <GitWorkflowControls
          sessionId={sessionId}
          workspaceRoot={workspaceRoot}
          status={status}
          disabled={workflowDisabled}
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
        {onSendReviewFeedback && (
          <Button
            variant="secondary"
            size="small"
            disabled={commentLoading || reviewFeedbackDisabled}
            onClick={() => void sendReviewFeedback()}
          >
            Send Review Feedback
          </Button>
        )}
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
              <Diff
                key={`${file.oldPath}:${file.newPath}`}
                viewType="unified"
                diffType={file.type}
                hunks={file.hunks}
                gutterEvents={{
                  onClick: ({ change, side }) => {
                    const anchor = change && commentAnchor(change, side);
                    if (anchor) setDraftAnchor(anchor);
                  },
                }}
                widgets={commentWidgets(
                  file.hunks.flatMap((hunk) => hunk.changes),
                  comments,
                  draftAnchor,
                  <div className="review-comment-draft">
                    <textarea
                      aria-label="Review comment"
                      value={draftBody}
                      onChange={(event) => setDraftBody(event.target.value)}
                      placeholder="Add review feedback…"
                      maxLength={16_384}
                    />
                    <div>
                      <Button
                        size="small"
                        variant="primary"
                        loading={commentLoading}
                        disabled={!draftBody.trim()}
                        onClick={() => void submitComment()}
                      >
                        Add Comment
                      </Button>
                      <Button
                        size="small"
                        variant="ghost"
                        onClick={() => {
                          setDraftAnchor(undefined);
                          setDraftBody("");
                        }}
                      >
                        Cancel
                      </Button>
                    </div>
                  </div>,
                  (commentId) => void removeComment(commentId),
                )}
              >
                {(hunks) => hunks.map((hunk) => <Hunk key={hunk.content} hunk={hunk} />)}
              </Diff>
            ))}
            {staleComments.length > 0 && (
              <aside className="review-stale-comments" aria-label="Stale review comments">
                <strong>Outdated comments</strong>
                {staleComments.map((comment) => (
                  <div key={comment.id}>
                    <span>{comment.side} line {comment.line}</span>
                    <p>{comment.body}</p>
                    <button type="button" onClick={() => void removeComment(comment.id)}>Delete</button>
                  </div>
                ))}
              </aside>
            )}
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
            <button type="button" onClick={() => onDelete(comment.id)}>Delete</button>
          </div>
        ))}
        {hasDraft ? draft : null}
      </div>
    );
  }
  return widgets;
}
