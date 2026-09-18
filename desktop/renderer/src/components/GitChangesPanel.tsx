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
import { useArtifacts } from "./ArtifactContext.js";
import { Button } from "./Button.js";
import { DropdownMenu, type DropdownMenuItem } from "./DropdownMenu.js";
import { LastTurnChanges, type ItemReviewStats } from "./LastTurnChanges.js";
import { GitWorkflowControls } from "./GitWorkflowControls.js";


type ReviewGroup = "staged" | "changes" | "untracked" | "conflicts";

type ReviewTab = "uncommitted" | "lastRun" | "entireTask";

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
  latestRunId?: string | undefined;
  previousItemId?: string | undefined;
  lastTurnRequest?: {
    runId: string;
    path?: string;
    itemId?: string;
    requestId: number;
  } | undefined;
  items?: import("../contracts.js").Item[] | undefined;
  workspaceRoot: string;
  scope: GitDiffScope;
  status: SessionGitStatus | undefined;
  summary?: SessionGitDiff | undefined;
  loading: boolean;
  error: string | undefined;
  expanded?: boolean | undefined;
  onScopeChange(scope: GitDiffScope): void;
  onRefresh(): void;
  readDiff?: (sessionId: string, scope: GitDiffScope, path?: string) => Promise<SessionGitDiff>;
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

function MoreHorizontalIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="5" cy="10" r="1.5" fill="currentColor" />
      <circle cx="10" cy="10" r="1.5" fill="currentColor" />
      <circle cx="15" cy="10" r="1.5" fill="currentColor" />
    </svg>
  );
}

function OpenInEditorIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M8 4H4v12h12v-4M11 4h5v5M15.5 4.5 9 11" />
    </svg>
  );
}

function OpenInWorkspaceIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M2.5 5h5l1.5 2h8.5v9.5h-15zM2.5 7h15" />
    </svg>
  );
}

export function GitChangesPanel(props: GitChangesPanelProps) {
  // 三段语义：
  // - 未提交：Git HEAD vs 工作区，与 session/run 无关；
  // - 最近一轮：最近一次 run 的 Item 补丁；
  // - 整个任务：本 session 全部 run 的 Item 补丁。
  const [reviewTab, setReviewTab] = useState<ReviewTab>("uncommitted");
  useEffect(() => {
    setReviewTab(props.lastTurnRequest ? "lastRun" : "uncommitted");
  }, [props.lastTurnRequest?.requestId, props.sessionId]);
  const isItemTab = reviewTab !== "uncommitted";
  const actions = useArtifacts();
  const summaryControlled = Object.prototype.hasOwnProperty.call(props, "summary");
  const {
    sessionId,
    workspaceRoot,
    scope,
    status,
    summary,
    loading,
    error,
    expanded = false,
    onScopeChange,
    onRefresh,
    readDiff = defaultReadDiff,
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

  const groups = useMemo<readonly FileGroup[]>(() => [
    { id: "staged", label: "已暂存", paths: status?.stagedFiles ?? [] },
    { id: "changes", label: "修改", paths: status?.unstagedFiles ?? [] },
    { id: "untracked", label: "未跟踪", paths: status?.untrackedFiles ?? [] },
    { id: "conflicts", label: "冲突", paths: status?.conflictFiles ?? [] },
  ], [status]);
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
  const [localError, setLocalError] = useState<string | undefined>(undefined);
  const [draft, setDraft] = useState<{ key: string; anchor: CommentAnchor }>();
  const [draftBody, setDraftBody] = useState("");
  const [commentLoading, setCommentLoading] = useState(false);
  const [itemStats, setItemStats] = useState<ItemReviewStats>({
    additions: 0,
    deletions: 0,
    count: 0,
    allExpanded: false,
  });
  const [itemExpandSignal, setItemExpandSignal] = useState<{ id: number; expand: boolean }>();
  const handleItemStats = useCallback((next: ItemReviewStats) => {
    setItemStats((current) => (
      current.additions === next.additions
      && current.deletions === next.deletions
      && current.count === next.count
      && current.allExpanded === next.allExpanded
        ? current
        : next
    ));
  }, []);
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
    // itemStats 由内层 LastTurnChanges 挂载后上报，不在此重置，
    // 否则子先报、父后清会把正确值覆盖回零。
  }, [scope, sessionId, reviewTab]);

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
    }).finally(() => { if (requestVersion.current === version) loadingKeys.current.delete(key); });
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
  const toolbarStats = isItemTab ? itemStats : { additions: stats.additions, deletions: stats.deletions };
  const toolbarAllExpanded = isItemTab ? itemStats.allExpanded : allFilesExpanded;
  const toolbarExpandDisabled = isItemTab ? itemStats.count === 0 : selections.length === 0;
  const fullScopeLabel = reviewTab === "lastRun"
    ? "最近一轮"
    : reviewTab === "entireTask"
      ? "整个任务"
      : "未提交";
  const currentScopeLabel = fullScopeLabel;

  const scopeMenuItems: DropdownMenuItem[] = [
    {
      key: "head",
      label: "未提交",
      disabled: reviewTab === "uncommitted",
      onClick: () => {
        setReviewTab("uncommitted");
        if (scope !== "head") onScopeChange("head");
      },
    },
    {
      key: "lastRun",
      label: "最近一轮",
      disabled: reviewTab === "lastRun",
      onClick: () => setReviewTab("lastRun"),
    },
    {
      key: "entireTask",
      label: "整个任务",
      disabled: reviewTab === "entireTask",
      onClick: () => setReviewTab("entireTask"),
    },
  ];

  const moreMenuItems: DropdownMenuItem[] = [
    ...(onCreateBranch
      ? [
          {
            key: "create-branch",
            label: "创建分支...",
            disabled: workflowDisabled || (status?.worktreeId === null && status?.dirty === true),
            onClick: () => onCreateBranch(),
          },
        ]
      : []),
    ...(onSendReviewFeedback
      ? [
          {
            key: "send-review-feedback",
            label: "发送审阅意见",
            disabled: commentLoading || reviewFeedbackDisabled,
            onClick: () => void sendReviewFeedback(),
          },
        ]
      : []),
  ];

  return (
    <section className={`git-changes-panel${expanded ? " git-changes-panel--expanded" : ""}`} aria-label="Git Changes">
      <header className="git-changes-toolbar" aria-label="审查工具栏">
        <div className="git-changes-toolbar-left">
          <DropdownMenu
            className="git-scope-dropdown"
            menuClassName="git-scope-dropdown-menu"
            label="Diff 范围"
            triggerAriaLabel="Diff 范围"
            trigger={(
              <span className="git-scope-dropdown-trigger" title={`切换变更范围: ${fullScopeLabel}`}>
                <span className="git-scope-dropdown-label">{currentScopeLabel}</span>
                <span className="dropdown-caret" aria-hidden="true">▾</span>
              </span>
            )}
            items={scopeMenuItems}
          />
          <div className="git-review-stats" aria-label="变更统计">
            <span className="git-review-stat git-review-stat--addition">+{toolbarStats.additions}</span>
            <span className="git-review-stat git-review-stat--deletion">-{toolbarStats.deletions}</span>
          </div>
        </div>

        <div className="git-changes-toolbar-right">
          <DropdownMenu
            className="git-more-dropdown"
            label="更多操作"
            triggerAriaLabel="更多操作"
            trigger={(
              <span className="git-icon-button git-more-trigger" title="更多操作">
                <MoreHorizontalIcon />
              </span>
            )}
            items={moreMenuItems}
          />
          <Button
            variant="ghost"
            size="small"
            className="git-icon-button"
            icon={<ExpandIcon collapse={toolbarAllExpanded} />}
            aria-label={toolbarAllExpanded ? "折叠全部差异" : "展开全部差异"}
            title={toolbarAllExpanded ? "折叠全部差异" : "展开全部差异"}
            disabled={toolbarExpandDisabled}
            onClick={() => {
              if (isItemTab) {
                setItemExpandSignal((current) => ({
                  id: (current?.id ?? 0) + 1,
                  expand: !itemStats.allExpanded,
                }));
                return;
              }
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
            <span className="sr-only">{toolbarAllExpanded ? "折叠全部差异" : "展开全部差异"}</span>
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
          {expanded && onSendReviewFeedback && (
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
          {status && (
            <GitWorkflowControls
              sessionId={sessionId}
              workspaceRoot={workspaceRoot}
              status={status}
              compact={true}
              expanded={expanded}
              disabled={workflowDisabled}
              openRequest={workflowOpenRequest}
              onRefresh={onRefresh}
              onCreateBranch={onCreateBranch}
            />
          )}
        </div>
      </header>
      {(error || summaryError || localError) && (
        <p className="approval-error git-review-error" role="alert">
          {localError ?? summaryError ?? error}
        </p>
      )}

      {isItemTab && (
        <LastTurnChanges
          key={`${sessionId}:${reviewTab}:${props.lastTurnRequest?.runId ?? props.latestRunId}:${props.lastTurnRequest?.requestId ?? ""}`}
          sessionId={sessionId}
          previousItemId={props.previousItemId}
          items={props.items ?? []}
          runId={props.lastTurnRequest?.runId ?? props.latestRunId}
          focusPath={props.lastTurnRequest?.path}
          onFeedback={onSendReviewFeedback}
          disabled={reviewFeedbackDisabled}
          entireTask={reviewTab === "entireTask"}
          onStats={handleItemStats}
          expandSignal={itemExpandSignal}
        />
      )}
      <div className="git-review-files" hidden={isItemTab}>
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
                      aria-label={path}
                      onClick={() => toggleFile(selection)}
                    >
                      <span className="git-file-disclosure"><FileDisclosureIcon expanded={expanded} /></span>
                      <code>{path}</code>
                      {hasFileStats && (
                        <span className="git-file-stats">
                          <span>+{fileStats.additions}</span>
                          <span>-{fileStats.deletions}</span>
                        </span>
                      )}
                    </button>
                    <Button
                      size="small"
                      variant="ghost"
                      className="git-icon-button"
                      icon={<OpenInEditorIcon />}
                      aria-label={`在编辑器中打开 ${path}`}
                      title={`在编辑器中打开 ${path}`}
                      onClick={() => {
                        setLocalError(undefined);
                        void openInEditor(sessionId, path).catch((cause: unknown) => {
                          setLocalError(userFacingError(cause));
                        });
                      }}
                    >
                      <span className="sr-only">{`在编辑器中打开 ${path}`}</span>
                    </Button>
                    <Button
                      size="small"
                      variant="ghost"
                      className="git-icon-button"
                      icon={<OpenInWorkspaceIcon />}
                      aria-label={`在工作区打开 ${path}`}
                      title={`在工作区打开 ${path}`}
                      onClick={() => actions?.openFile(path)}
                    >
                      <span className="sr-only">{`在工作区打开 ${path}`}</span>
                    </Button>
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
