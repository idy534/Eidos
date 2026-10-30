import { highlightText, clearHighlights } from "./find-text.js";
import {
  formatInlineReferencesAsPlainText,
  removeInlineReferenceTokenById,
} from "../../../shared/inline-references.js";
import {
  InlineReferenceProse,
  InputReferenceCards,
  isInlineReference,
  isShelfReference,
  useReferenceActivation,
} from "./InputContext.js";
import { InlineRichEditor } from "./InlineRichEditor.js";
import { ToolTextView } from "./ToolTextView.js";
import { toolFilePaths } from "./ResultFiles.js";
import { Fragment, memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import stripAnsi from "strip-ansi";
import { artifactPath, useArtifacts, usePreviewUrl } from "./ArtifactContext.js";

import type {
  ApprovalRequest,
  Item,
  ModelOption,
  ResponseActionState,
  ResponseFeedbackValue,
  Run,
  ToolCall,
} from "../contracts.js";
import type { AgentSummary } from "../../../shared/collaboration.generated.js";
import { terminalRunPresentation } from "../session-state.js";
import { MarkdownContent } from "./MarkdownContent.js";
import { ApprovalRecoveryBanner } from "./ApprovalRecoveryBanner.js";
import { DropdownMenu } from "./DropdownMenu.js";
import { CopyIcon, CheckmarkIcon, AutomationsIcon, CloseIcon, ChevronDownIcon } from "./MenuIcons.js";
import { ForkIcon } from "./ForkIcon.js";
import { TurnResults } from "./TurnResults.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { DiffViewer, parseMultiFileDiff } from "./DiffViewer.js";
import { AutomationToolCard } from "./automations/AutomationToolCard.js";
import { Tooltip } from "./Tooltip.js";
import { SkillIcon, AgentIcon as SubagentToolIcon, MemoryIcon } from "./InputPicker.js";
import { SkillDetailDialog } from "./settings/SkillDetailDialog.js";
import { useFeedScroll } from "./useFeedScroll.js";
import { ContextCompactionRow } from "./ContextCompactionRow.js";
import { HookActivityRows } from "./HookActivityRows.js";
import { ConversationRunRail } from "./ConversationRunRail.js";
import type { ContextCompactionActivity } from "../../../shared/context.generated.js";
export { isFeedAtBottom } from "./useFeedScroll.js";


type FeedbackHandler = (
  itemId: string,
  feedback: ResponseFeedbackValue | null,
) => Promise<void>;
type RegenerateHandler = (run: Run) => Promise<void>;
type EditResendHandler = (run: Run, editedInput: string, references?: string[]) => Promise<boolean | void>;

interface Props {
  searchTarget?: { itemId: string; query: string; occurrence: number; revision: number } | undefined;
  contextCompactions?: readonly ContextCompactionActivity[] | undefined;
  items: Item[];
  resultItems?: Item[];
  runs: Run[];
  allRuns?: Run[] | undefined;
  onEnsureItemMounted?: ((itemId: string) => Promise<boolean>) | undefined;
  models?: ModelOption[];
  workspaceRoot?: string;
  responseActionState?: ResponseActionState;
  pendingFeedbackItemIds?: ReadonlySet<string>;
  revisionSubmitting?: boolean;
  approvals: ApprovalRequest[];
  approvalLoadError?: string | undefined;
  loadingPendingApprovals?: boolean | undefined;
  onRetryLoadPending?: (() => void) | undefined;
  onFeedback?: FeedbackHandler;
  onFork?: ((item: Item) => void) | undefined;
  onLoadOlder?: (() => void) | undefined;
  loadingOlder?: boolean;
  onRegenerate?: RegenerateHandler;
  onEditResend?: EditResendHandler;
  onOpenFile?: ((path: string) => void) | undefined;
  onOpenPlan?: (() => void) | undefined;
  agents?: AgentSummary[] | undefined;
  onOpenSubagent?: ((target?: { id?: string | undefined; taskName?: string | undefined }) => void) | undefined;
  allowFeedback?: boolean | undefined;
  allowRegenerate?: boolean | undefined;
  allowEditResend?: boolean | undefined;
}

interface Segment {
  user: Item | undefined;
  process: ProcessEntry[];
  response: Item[];
}

type ProcessEntry = { kind: "item"; item: Item } | { kind: "compaction"; activity: ContextCompactionActivity };
const EMPTY_COMPACTIONS: readonly ContextCompactionActivity[] = [];

const ACTIVE_RUN_STATUSES = new Set<Run["status"]>([
  "queued", "running", "waiting_input", "waiting_agents", "waiting_approval", "finalizing",
]);

const TERMINAL_RUN_STATUSES = new Set<Run["status"]>([
  "stopped", "succeeded", "failed", "canceled", "interrupted",
]);

const EMPTY_RESPONSE_ACTION_STATE: ResponseActionState = {
  feedback: [],
  revisions: [],
};
const EMPTY_PENDING_FEEDBACK = new Set<string>();
const NOOP_FEEDBACK: FeedbackHandler = async () => {};
const NOOP_REGENERATE: RegenerateHandler = async () => {};
const NOOP_EDIT_RESEND: EditResendHandler = async () => {};


export const ExecutionFeed = memo(function ExecutionFeed({
  searchTarget,
  contextCompactions = EMPTY_COMPACTIONS,
  items,
  resultItems = items,
  runs,
  allRuns,
  onEnsureItemMounted,
  models = [],
  workspaceRoot = "",
  responseActionState = EMPTY_RESPONSE_ACTION_STATE,
  pendingFeedbackItemIds = EMPTY_PENDING_FEEDBACK,
  revisionSubmitting = false,
  approvals,
  approvalLoadError,
  loadingPendingApprovals,
  onRetryLoadPending,
  onFeedback = NOOP_FEEDBACK,
  onFork,
  onLoadOlder,
  loadingOlder = false,
  onRegenerate = NOOP_REGENERATE,
  onEditResend = NOOP_EDIT_RESEND,
  onOpenFile,
  onOpenPlan,
  agents,
  onOpenSubagent,
  allowFeedback = true,
  allowRegenerate = true,
  allowEditResend = true,
}: Props) {
  const {
    feedRef, contentRef, atBottom, following, scheduleFollow, jumpToBottom, pauseFollowing,
    handleScroll, handleWheel, handleKeyDown, handlePointerDown, handleTouchStart, handleTouchMove,
  } = useFeedScroll(items.length > 0, {
    onLoadOlder,
    loadingOlder,
    firstItemId: items[0]?.id,
  });

  useLayoutEffect(scheduleFollow, [items, runs, contextCompactions, responseActionState.revisions, scheduleFollow]);

  const supersededRunIds = useMemo(
    () => new Set(responseActionState.revisions.map((revision) => revision.sourceRunId)),
    [responseActionState.revisions],
  );
  const feedbackByItemId = useMemo(
    () => new Map(responseActionState.feedback.map((entry) => [entry.itemId, entry.value])),
    [responseActionState.feedback],
  );
  const latestVisibleRun = [...runs].reverse().find((run) => !supersededRunIds.has(run.id));
  const hasActiveRun = runs.some((run) => ACTIVE_RUN_STATUSES.has(run.status));

  useEffect(() => {
    if (!searchTarget) { clearHighlights("eidos-chat-find"); return; }
    pauseFollowing();
    const element = document.getElementById(`item-${searchTarget.itemId}`);
    if (!element) return;
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
    }
    const body = element.querySelector<HTMLElement>(".eidos-chat-prose");
    if (body) highlightText(body, searchTarget.query, searchTarget.occurrence);
    return () => clearHighlights("eidos-chat-find");
  }, [searchTarget, items]);

  const runsById = new Map(runs.map((run) => [run.id, run]));
  const itemGroups = groupItemsByRun(items).filter(({ runId }) => (!supersededRunIds.has(runId) || items.some((item) => item.runId === runId && item.id === searchTarget?.itemId)));

  return (
    <div className="feed-shell">
      <ConversationRunRail
        runs={allRuns ?? runs}
        items={items}
        resultItems={resultItems}
        supersededRunIds={supersededRunIds}
        contextCompactions={contextCompactions}
        feedRef={feedRef}
        pauseFollowing={pauseFollowing}
        onEnsureItemMounted={onEnsureItemMounted}
      />
      {approvalLoadError && onRetryLoadPending && (
        <ApprovalRecoveryBanner
          error={approvalLoadError}
          loading={loadingPendingApprovals}
          onRetry={onRetryLoadPending}
        />
      )}
      <section
        ref={feedRef}
        className="feed"
        data-following-bottom={following}
        aria-label="Execution Feed"
        aria-live="polite"
        onScroll={handleScroll}
        onWheel={handleWheel}
        onKeyDown={handleKeyDown}
        onPointerDown={handlePointerDown}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
      >
        <div className="feed-content" ref={contentRef}>
          {loadingOlder && (
            <div className="feed-loading-older" role="status" aria-live="polite">
              <span className="feed-loading-spinner" aria-hidden="true" />
              <span>正在读取历史…</span>
            </div>
          )}
          {itemGroups.map(({ runId, items: runItems }) => {
            const run = runsById.get(runId);
            if (!run) return null;
            const segments = splitRunIntoSegments(runItems, contextCompactions.filter((activity) => activity.runId === runId));
            const canReviseRun = !run.inherited && latestVisibleRun?.id === run.id
              && TERMINAL_RUN_STATUSES.has(run.status)
              && !hasActiveRun
              && !revisionSubmitting;
            const modelName = models.find((model) => model.id === run.modelId)?.name ?? run.remoteModelId ?? run.modelId;
            return (
              <Fragment key={runId}>
                {segments.map((segment, index) => (
                  <RunSegment
                    key={`${runId}:${segment.user?.id ?? index}`}
                    segment={segment}
                    run={run}
                    resultItems={resultItems}
                    modelName={modelName}
                    workspaceRoot={workspaceRoot}
                    isLast={index === segments.length - 1}
                    canReviseRun={canReviseRun}
                    feedbackByItemId={feedbackByItemId}
                    pendingFeedbackItemIds={pendingFeedbackItemIds}
                    revisionSubmitting={revisionSubmitting}
                    approvals={approvals}
                    onFeedback={onFeedback}
                    onFork={onFork}
                    onRegenerate={onRegenerate}
                    onEditResend={onEditResend}
                    onOpenFile={onOpenFile}
                    onOpenPlan={onOpenPlan}
                    agents={agents}
                    onOpenSubagent={onOpenSubagent}
                    allowFeedback={allowFeedback}
                    allowRegenerate={allowRegenerate}
                    allowEditResend={allowEditResend}
                    isSearchTarget={searchTarget?.itemId === segment.user?.id}
                  />
                ))}
                <RunNotice run={run} />
                <HookActivityRows run={run} />
              </Fragment>
            );
          })}
        </div>
      </section>
      <button
        className="feed-jump-to-bottom"
        type="button"
        aria-label="滚动到最新内容"
        hidden={atBottom}
        onClick={jumpToBottom}
      >
        <span aria-hidden="true">↓</span>
      </button>
    </div>
  );
});

const RunSegment = memo(function RunSegment({
  segment,
  run,
  resultItems,
  modelName,
  workspaceRoot,
  isLast,
  canReviseRun,
  feedbackByItemId,
  pendingFeedbackItemIds,
  revisionSubmitting,
  approvals,
  onFeedback,
  onFork,
  onRegenerate,
  onEditResend,
  onOpenFile,
  onOpenPlan,
  agents,
  onOpenSubagent,
  allowFeedback = true,
  allowRegenerate = true,
  allowEditResend = true,
  isSearchTarget = false,
}: {
  segment: Segment;
  run: Run;
  resultItems: Item[];
  modelName: string;
  workspaceRoot?: string | undefined;
  isLast: boolean;
  canReviseRun: boolean;
  feedbackByItemId: ReadonlyMap<string, ResponseFeedbackValue>;
  pendingFeedbackItemIds: ReadonlySet<string>;
  revisionSubmitting: boolean;
  approvals: ApprovalRequest[];
  onFeedback: FeedbackHandler;
  onFork?: ((item: Item) => void) | undefined;
  onRegenerate: RegenerateHandler;
  onEditResend: EditResendHandler;
  onOpenFile?: ((path: string) => void) | undefined;
  onOpenPlan?: (() => void) | undefined;
  agents?: AgentSummary[] | undefined;
  onOpenSubagent?: ((target?: { id?: string | undefined; taskName?: string | undefined }) => void) | undefined;
  allowFeedback?: boolean | undefined;
  allowRegenerate?: boolean | undefined;
  allowEditResend?: boolean | undefined;
  isSearchTarget?: boolean | undefined;
}) {
  // Hidden observations still determine which assistant messages are progress.
  const visibleProcess = segment.process.filter((entry) => {
    if (entry.kind === "compaction") return true;
    const item = entry.item;
    if (item.toolCall?.toolName !== "write_stdin") return true;
    const data = objectField(parseObject(item.toolCall.resultJson), "data");
    return item.status === "failed" && !stringField(data, "executionStatus");
  });
  const showThinking = isLast
    && ACTIVE_RUN_STATUSES.has(run.status)
    && visibleProcess.length === 0
    && segment.response.length === 0;

  return (
    <>
      {segment.user && (
        <UserMessage
          item={segment.user}
          run={run}
          canEdit={isLast && canReviseRun && allowEditResend}
          revisionSubmitting={revisionSubmitting}
          onEditResend={onEditResend}
          isSearchTarget={isSearchTarget}
        />
      )}
      {visibleProcess.length > 0 && (
        <ProcessGroup
          run={run}
        >
          {visibleProcess.map((entry) => entry.kind === "compaction" ? (
            <ContextCompactionRow key={`compaction:${entry.activity.requestId}`} activity={entry.activity} run={run} />
          ) : (
            <ProcessItem
              key={entry.item.id}
              item={entry.item}
              run={run}
              approval={approvals.find((request) => request.itemId === entry.item.id)}
              onOpenFile={onOpenFile}
              agents={agents}
              onOpenSubagent={onOpenSubagent}
            />
          ))}
        </ProcessGroup>
      )}
      {showThinking && <p className="thinking-indicator" role="status">正在思考</p>}
      {segment.response.map((item, index) => {
        if (item.toolCall?.toolName === "write_plan") {
          return (
            <PlanResponseItem
              key={item.id}
              item={item}
              onOpenPlan={onOpenPlan}
            />
          );
        }
        return (
          <AssistantMessage
            key={item.id}
            item={item}
            run={run}
            modelName={modelName}
            workspaceRoot={workspaceRoot}
            feedback={feedbackByItemId.get(item.id)}
            feedbackPending={pendingFeedbackItemIds.has(item.id)}
            canRegenerate={allowRegenerate && isLast && index === segment.response.length - 1 && canReviseRun}
            allowFeedback={allowFeedback && !item.inherited}
            isFinal={isLast && index === segment.response.length - 1 && TERMINAL_RUN_STATUSES.has(run.status)}
            showTurnResults={isLast
              && index === segment.response.length - 1
              && TERMINAL_RUN_STATUSES.has(run.status)
              && segment.response.every((responseItem) => responseItem.status !== "in_progress")}
            resultItems={resultItems}
            showTextChanges={true}
            revisionSubmitting={revisionSubmitting}
            onFeedback={onFeedback}
            onFork={onFork}
            onRegenerate={onRegenerate}
          />
        );
      })}
      {isLast
        && segment.response.length === 0
        && TERMINAL_RUN_STATUSES.has(run.status)
        && <TurnResults run={run} items={resultItems} showTextChanges={true} className="turn-results--standalone" />}
    </>
  );
});

function ProcessGroup({ run, children }: { run: Run; children: ReactNode }) {
  const terminal = TERMINAL_RUN_STATUSES.has(run.status);
  const [open, setOpen] = useState(!terminal);
  const prevTerminalRef = useRef(terminal);

  useEffect(() => {
    if (!prevTerminalRef.current && terminal) {
      setOpen(false);
    }
    prevTerminalRef.current = terminal;
  }, [terminal]);

  return (
    <details className="process-group" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary><ProcessLabel run={run} /></summary>
      <div className="process-content">{children}</div>
    </details>
  );
}

export function formatItemTime(timestampMs?: number): string {
  if (!timestampMs) return "";
  const date = new Date(timestampMs);
  if (Number.isNaN(date.getTime())) return "";
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  return `${month}月${day}日 ${hours}:${minutes}`;
}

function CopyButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!content) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(content);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy text:", err);
    }
  };

  return (
    <Tooltip content={copied ? "已复制" : "复制内容"}>
      <button
        type="button"
        className="response-action-button feed-item-copy-btn"
        onClick={handleCopy}
        title={copied ? "已复制" : "复制内容"}
        aria-label={copied ? "已复制" : "复制内容"}
      >
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          {copied ? (
            <polyline points="20 6 9 17 4 12" />
          ) : (
            <>
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </>
          )}
        </svg>
      </button>
    </Tooltip>
  );
}

function ShellCopyButton({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (event: React.MouseEvent) => {
    event.stopPropagation();
    if (!command) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(command);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy command:", err);
    }
  };

  return (
    <Tooltip content={copied ? "已复制命令" : "复制命令"}>
      <button
        type="button"
        className={`shell-copy-button${copied ? " is-copied" : ""}`}
        onClick={handleCopy}
        title={copied ? "已复制命令" : "复制命令"}
        aria-label={copied ? "已复制命令" : "复制命令"}
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          {copied ? (
            <polyline points="20 6 9 17 4 12" />
          ) : (
            <>
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </>
          )}
        </svg>
      </button>
    </Tooltip>
  );
}

const USER_MESSAGE_MAX_COLLAPSED_HEIGHT = 300;
const USER_MESSAGE_OVERFLOW_TOLERANCE = 30;

function UserMessage({
  item,
  run,
  canEdit,
  revisionSubmitting,
  onEditResend,
  isSearchTarget = false,
}: {
  item: Item;
  run: Run;
  canEdit: boolean;
  revisionSubmitting: boolean;
  onEditResend: EditResendHandler;
  isSearchTarget?: boolean | undefined;
}) {
  const formattedTime = formatItemTime(item.completedAt ?? item.createdAt);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.content ?? "");
  const [draftReferences, setDraftReferences] = useState(item.references ?? []);
  const [expanded, setExpanded] = useState(false);
  const [isOverflowing, setIsOverflowing] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isSearchTarget) {
      setExpanded(true);
    }
  }, [isSearchTarget]);

  useLayoutEffect(() => {
    const el = contentRef.current;
    if (!el) return;

    const checkOverflow = () => {
      const hasOverflow = el.scrollHeight > USER_MESSAGE_MAX_COLLAPSED_HEIGHT + USER_MESSAGE_OVERFLOW_TOLERANCE;
      setIsOverflowing(hasOverflow);
    };

    checkOverflow();

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(checkOverflow);
      observer.observe(el);
      return () => observer.disconnect();
    }
  }, [item.content, item.references]);

  const handleToggleExpand = () => {
    if (expanded) {
      setExpanded(false);
      contentRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } else {
      setExpanded(true);
    }
  };

  useEffect(() => {
    if (!editing) { setDraft(item.content ?? ""); setDraftReferences(item.references ?? []); }
  }, [editing, item.content, item.references]);

  async function submitEdit(): Promise<void> {
    const value = draft.trim();
    if ((!value && !draftReferences.length) || revisionSubmitting) return;
    const accepted = await onEditResend(run, value, draftReferences.map((reference) => reference.id));
    if (accepted !== false) setEditing(false);
  }

  const activeReferences = (editing ? draftReferences : item.references) ?? [];
  const shelfReferences = activeReferences.filter(isShelfReference);
  const inlineReferences = activeReferences.filter(isInlineReference);
  const {
    activateReference: activateInlineReference,
    dialogs: inlineActivationDialogs,
  } = useReferenceActivation();
  const removeDraftReference = (id: string) => {
    setDraft((previous) => removeInlineReferenceTokenById(previous, id));
    setDraftReferences((previous) => previous.filter((reference) => reference.id !== id));
  };

  return (
    <div id={`item-${item.id}`} className="feed-item feed-item--user">
      {run.channelOrigin && <p className="feed-label">{run.channelOrigin.channel === "feishu" ? "飞书" : run.channelOrigin.channel === "wecom" ? "企业微信" : "钉钉"}消息</p>}
      {run.automationOrigin && <p className="feed-label">定时任务自动执行</p>}
      <div className="user-message-bubble">
        {shelfReferences.length > 0 ? (
          <InputReferenceCards
            references={shelfReferences}
            variant={editing ? "composer" : "bubble"}
            placement="shelf"
            {...(editing ? { onRemove: removeDraftReference } : {})}
          />
        ) : null}
        {editing ? (
          <div className="user-message-editor">
            {inlineReferences.length > 0 ? (
              <>
                <InlineRichEditor
                  autoFocus
                  value={draft}
                  inlineReferences={inlineReferences}
                  disabled={revisionSubmitting}
                  ariaLabel="编辑最近一次提问"
                  onChange={(nextValue) => setDraft(nextValue)}
                  onRemoveReference={removeDraftReference}
                  onActivateReference={(reference) => void activateInlineReference(reference)}
                  onSubmit={() => void submitEdit()}
                  onEscape={() => setEditing(false)}
                />
                {inlineActivationDialogs}
              </>
            ) : (
              <textarea
                autoFocus
                value={draft}
                disabled={revisionSubmitting}
                aria-label="编辑最近一次提问"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setEditing(false);
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void submitEdit();
                  }
                }}
              />
            )}
            <div className="user-message-editor-actions">
              <button type="button" disabled={revisionSubmitting} onClick={() => setEditing(false)}>取消</button>
              <button
                type="button"
                className="user-message-editor-submit"
                disabled={revisionSubmitting || (!draft.trim() && !draftReferences.length)}
                onClick={() => void submitEdit()}
              >
                {revisionSubmitting ? "发送中…" : "发送"}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div
              ref={contentRef}
              className={`user-message-collapsible ${isOverflowing && !expanded ? "is-collapsed" : ""}`}
            >
              {inlineReferences.length > 0 ? (
                <InlineReferenceProse
                  content={item.content ?? ""}
                  references={inlineReferences}
                  variant="bubble"
                />
              ) : item.content ? (
                <p className="eidos-chat-prose">{item.content}</p>
              ) : null}
              {isOverflowing && !expanded && (
                <div className="user-message-fade-mask" aria-hidden="true" />
              )}
            </div>
            {isOverflowing && (
              <button
                type="button"
                className="user-message-expand-btn"
                aria-expanded={expanded}
                onClick={handleToggleExpand}
              >
                <span>{expanded ? "收起" : "显示更多"}</span>
                <ChevronDownIcon className={`user-message-expand-icon ${expanded ? "is-expanded" : ""}`} />
              </button>
            )}
          </>
        )}
      </div>
      {!editing && (
        <div className="feed-item-footer response-footer">
          <div className="response-actions-left">
            {item.content && <CopyButton content={formatInlineReferencesAsPlainText(item.content)} />}
            {canEdit && (item.content || item.references?.length) && (
              <ActionButton
                label="编辑并重新发送"
                disabled={revisionSubmitting}
                onClick={() => setEditing(true)}
              >
                <EditIcon />
              </ActionButton>
            )}
          </div>
          {formattedTime && <span className="feed-item-timestamp">{formattedTime}</span>}
        </div>
      )}
    </div>
  );
}

function AssistantMessage({
  item,
  run,
  modelName,
  workspaceRoot = "",
  feedback,
  feedbackPending,
  canRegenerate,
  allowFeedback = true,
  isFinal,
  showTurnResults,
  resultItems,
  showTextChanges,
  revisionSubmitting = false,
  onFeedback,
  onFork,
  onRegenerate,
}: {
  item: Item;
  run: Run;
  modelName: string;
  workspaceRoot?: string | undefined;
  feedback: ResponseFeedbackValue | undefined;
  feedbackPending: boolean;
  canRegenerate: boolean;
  allowFeedback?: boolean | undefined;
  isFinal: boolean;
  showTurnResults: boolean;
  resultItems: Item[];
  showTextChanges: boolean;
  revisionSubmitting?: boolean | undefined;
  onFeedback: FeedbackHandler;
  onFork?: ((item: Item) => void) | undefined;
  onRegenerate: RegenerateHandler;
}) {
  const [confirmRegenerateOpen, setConfirmRegenerateOpen] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const formattedTime = formatItemTime(item.completedAt ?? item.createdAt);
  const isStreaming = item.status === "in_progress";

  const canFeedback = allowFeedback && item.status === "completed" && Boolean(item.content);

  useEffect(() => {
    if (!canRegenerate) {
      setConfirmRegenerateOpen(false);
    }
  }, [canRegenerate]);

  return (
    <article id={`item-${item.id}`} className="feed-item feed-item--assistant" ref={contentRef}>
      <MarkdownContent className="eidos-chat-prose" content={item.content || ""} isStreaming={isStreaming} />
      {!isStreaming && item.contentBytes !== undefined && item.contentHash && <ToolTextView
        sessionId={item.sessionId} toolCallId={item.id} field="content"
        sha256={item.contentHash} totalBytes={item.contentBytes} />}


      {(item.incomplete || item.status === "canceled") && <p role="status">回答未完成</p>}
      {showTurnResults && <TurnResults run={run} items={resultItems} showTextChanges={showTextChanges} />}
      {isFinal && item.content && (
        <div className="feed-item-footer response-footer">
          <div className="response-actions-left">
            <CopyButton content={item.content} />
            {onFork && item.status === "completed" && !item.incomplete && (
              <ActionButton label="从这里分叉" onClick={() => onFork(item)}>
                <ForkIcon />
              </ActionButton>
            )}
            {allowFeedback && (
              <>
                <ActionButton
                  label={feedback === "up" ? "取消点赞" : "点赞"}
                  active={feedback === "up"}
                  className="response-action-button--thumb-up"
                  disabled={!canFeedback || feedbackPending}
                  onClick={() => void onFeedback(item.id, feedback === "up" ? null : "up")}
                >
                  <ThumbUpIcon />
                </ActionButton>
                <ActionButton
                  label={feedback === "down" ? "取消差评" : "差评"}
                  active={feedback === "down"}
                  className="response-action-button--thumb-down"
                  disabled={!canFeedback || feedbackPending}
                  onClick={() => void onFeedback(item.id, feedback === "down" ? null : "down")}
                >
                  <ThumbDownIcon />
                </ActionButton>
              </>
            )}
            {canRegenerate && (
              <>
                <ActionButton
                  label="重新回答"
                  className="response-action-button--regenerate"
                  disabled={revisionSubmitting}
                  onClick={() => setConfirmRegenerateOpen(true)}
                >
                  <RegenerateIcon />
                </ActionButton>
                <ConfirmDialog
                  open={confirmRegenerateOpen}
                  title="重新回答？"
                  description="重新回答将放弃当前的回答并重新生成。确定要继续吗？"
                  confirmLabel="重新回答"
                  cancelLabel="取消"
                  isDestructive={false}
                  busy={revisionSubmitting}
                  onConfirm={() => {
                    setConfirmRegenerateOpen(false);
                    void onRegenerate(run);
                  }}
                  onCancel={() => setConfirmRegenerateOpen(false)}
                />
              </>
            )}
            {!item.inherited && <MoreActionsDropdown
              session={run.sessionId}
              run={run.id}
              item={item.id}
              step={item.modelStepIndex ?? run.modelStepCount ?? 1}
              model={run.modelId}
              workspace={workspaceRoot}
            />}
          </div>
          <div className="response-meta">
            <Tooltip className="response-model-tooltip" content={`本次回复模型：${modelName}`}>
              <span className="response-model" title={`本次回复模型：${modelName}`}>{modelName}</span>
            </Tooltip>
            {formattedTime && <span className="feed-item-timestamp">{formattedTime}</span>}
          </div>
        </div>
      )}
    </article>
  );
}

function MoreHorizontalIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
      <circle cx="5" cy="12" r="1" />
    </svg>
  );
}

function MoreActionsDropdown({
  session,
  run,
  item,
  step,
  model,
  workspace,
}: {
  session: string;
  run: string;
  item: string;
  step: number;
  model: string;
  workspace: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopyRequestId = async () => {
    const payload = {
      session,
      run,
      item,
      step,
      model,
      workspace,
    };
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Failed to copy request ID:", err);
    }
  };

  return (
    <DropdownMenu
      className="response-more-dropdown"
      trigger={<MoreHorizontalIcon />}
      title={copied ? "已复制请求ID" : "更多操作"}
      triggerAriaLabel="更多操作"
      label="更多操作菜单"
      items={[
        {
          key: "copy-request-id",
          label: copied ? "已复制" : "复制请求ID",
          icon: copied ? <CheckmarkIcon /> : <CopyIcon />,
          onClick: () => {
            void handleCopyRequestId();
          },
        },
      ]}
    />
  );
}

function ActionButton({
  label,
  active = false,
  disabled = false,
  className = "",
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  className?: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip content={label}>
      <button
        type="button"
        className={`response-action-button${active ? " response-action-button--active" : ""}${className ? ` ${className}` : ""}`}
        disabled={disabled}
        title={label}
        aria-label={label}
        aria-pressed={active || undefined}
        onClick={onClick}
      >
        {children}
      </button>
    </Tooltip>
  );
}

function ThumbUpIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M7 10v10H4a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h3Z" />
      <path d="M7 20h9.2a2 2 0 0 0 1.9-1.4l2-6A2 2 0 0 0 18.2 10H14l.7-3.4A2.2 2.2 0 0 0 12.5 4L7 10Z" />
    </svg>
  );
}

function ThumbDownIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M7 14V4H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h3Z" />
      <path d="M7 4h9.2a2 2 0 0 1 1.9 1.4l2 6a2 2 0 0 1-1.9 2.6H14l.7 3.4a2.2 2.2 0 0 1-2.2 2.6L7 14Z" />
    </svg>
  );
}

function RegenerateIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 11a8 8 0 0 0-14.9-4" />
      <path d="M4 4v4h4" />
      <path d="M4 13a8 8 0 0 0 14.9 4" />
      <path d="M20 20v-4h-4" />
    </svg>
  );
}

function EditIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4 11.5-11.5Z" />
    </svg>
  );
}

function ProcessItem({
  item,
  run,
  approval,
  onOpenFile,
  agents,
  onOpenSubagent,
}: {
  item: Item;
  run: Run;
  approval: ApprovalRequest | undefined;
  onOpenFile?: ((path: string) => void) | undefined;
  agents?: AgentSummary[] | undefined;
  onOpenSubagent?: ((target?: { id?: string | undefined; taskName?: string | undefined }) => void) | undefined;
}) {
  if (item.kind === "user_message") {
    const steeringReferences = item.references ?? [];
    const steeringShelf = steeringReferences.filter(isShelfReference);
    const steeringInline = steeringReferences.filter(isInlineReference);
    return (
      <div id={`item-${item.id}`} className="process-steering" data-status={item.status}>
        <p className="feed-label">引导对话</p>
        <div className="user-message-bubble">
          {steeringShelf.length > 0 ? (
            <InputReferenceCards
              references={steeringShelf}
              variant="bubble"
              placement="shelf"
            />
          ) : null}
          {steeringInline.length > 0 ? (
            <InlineReferenceProse
              content={item.content ?? ""}
              references={steeringInline}
              variant="bubble"
            />
          ) : item.content ? (
            <MarkdownContent className="eidos-chat-prose" content={item.content} />
          ) : null}
        </div>
      </div>
    );
  }
  if (item.kind === "assistant_message") {
    if (!item.content) return null;
    return <div id={`item-${item.id}`} className="process-text"><MarkdownContent className="eidos-chat-prose" content={item.content} isStreaming={item.status === "in_progress"} />
      {item.status !== "in_progress" && item.contentBytes !== undefined && item.contentHash && <ToolTextView
        sessionId={item.sessionId} toolCallId={item.id} field="content" sha256={item.contentHash} totalBytes={item.contentBytes} />}
    </div>;
  }
  if (!item.toolCall) return null;

  if (item.toolCall.toolName === "write_stdin") {
    return item.status === "failed" ? <p className="shell-error-summary">命令跟进失败 · {stringField(parseObject(item.toolCall.resultJson), "code") || "暂时无法读取命令状态"}</p> : null;
  }

  const toolItem = item.toolCall.toolName === "run_shell"
    ? <ShellItem item={item} toolCall={item.toolCall} />
    : <ToolItem item={item} toolCall={item.toolCall} onOpenFile={onOpenFile} agents={agents} onOpenSubagent={onOpenSubagent} />;

  if (approval && run.approvalMode !== "full_access") {
    return <><p className="feed-label">需要批准 · {{ file_change: "文件变更", command_execution: "Shell 命令", network_access: "网络访问", external_tool: "MCP 工具", permission_request: "权限申请" }[approval.kind]} · <span>{approval.summary}</span></p>{toolItem}</>;
  }
  if (item.toolCall.approvalDecision && run.approvalMode !== "full_access") {
    return <div>{item.toolCall.toolName !== "request_permissions" && <p className="feed-label">{run.approvalMode === "auto_review" ? "审批 · " : ""}{item.toolCall.approvalDecision === "approve" ? "已批准" : "已拒绝"} · {item.toolCall.toolName}</p>}
      {item.toolCall.approvalFeedback && <p className="feed-label">{item.toolCall.approvalFeedback}</p>}
      {toolItem}</div>;
  }

  return toolItem;
}


export interface ShellOutputSegment {
  source: "stream" | "stdout" | "stderr";
  content: string;
}

/**
 * Choose the one output representation that the feed should render.
 *
 * New Shell items receive ordered, cumulative deltas in Item.content. The
 * final result still carries separate stdout/stderr fields for compatibility,
 * but rendering both after a stream would duplicate the output and lose its
 * receive order. Empty and missing content are treated as legacy records, so
 * their final streams remain visible.
 */
export function shellOutputSegments(
  accumulatedContent: string | undefined,
  stdout: string,
  stderr: string,
): ShellOutputSegment[] {
  if (accumulatedContent) {
    const content = stripAnsi(accumulatedContent);
    return content ? [{ source: "stream", content }] : [];
  }

  return [
    { source: "stdout" as const, content: stripAnsi(stdout) },
    { source: "stderr" as const, content: stripAnsi(stderr) },
  ].filter((segment) => Boolean(segment.content));
}

function ShellItem({ item, toolCall }: { item: Item; toolCall: ToolCall }) {
  const args = parseObject(toolCall.argumentsJson);
  const result = parseObject(toolCall.resultJson);
  const data = objectField(result, "data");
  const running = !item.inherited && (item.status === "in_progress" || stringField(data, "executionStatus") === "running");
  const command = stringField(args, "command") || stringArrayField(args, "argv").join(" ") || "Shell 命令";
  const stdout = stringField(data, "stdout");
  const stderr = stringField(data, "stderr");
  const exitCode = numberField(data, "exitCode");
  const code = stringField(result, "code");
  const summary = stringField(result, "summary");
  const gateRejected = isReconciliationGate(result);
  const reconciliationRequired = result.reconciliationRequired === true;
  const truncated = booleanField(data, "truncated");
  const truncationReason = stringField(data, "truncationReason");
  const outputSegments = shellOutputSegments(item.content, stdout, stderr);
  const hasOutput = outputSegments.length > 0;
  const pendingVerification = reconciliationRequired && !gateRejected;
  const failed = result.outcome === "error"
    || item.status === "failed"
    || (exitCode !== undefined && exitCode !== 0);
  const success = !gateRejected
    && !running
    && item.status === "completed"
    && result.outcome !== "error"
    && !pendingVerification
    && (exitCode === undefined || exitCode === 0);
  const statusTone = running
    ? "neutral"
    : pendingVerification
      ? "warning"
      : success
        ? "success"
        : "error";
  const statusText = running
    ? "运行中"
    : gateRejected
      ? "未执行"
      : pendingVerification
        ? "结果未知"
        : success
          ? "✓ 成功"
          : failed
            ? "失败"
            : statusLabel(item.status);
  const [open, setOpen] = useState(false);

  const isExitNonzero = code === "shell_exit_nonzero" || code === "nonzero_exit";
  const showErrorCode = !running
    && !gateRejected
    && !pendingVerification
    && !success
    && Boolean(code)
    && !isExitNonzero;
  const showErrorSummary = !running
    && !success
    && !pendingVerification
    && Boolean(summary)
    && !isExitNonzero
    && !summary?.startsWith("Command did not succeed");
  const showExitCode = !running
    && !gateRejected
    && !pendingVerification
    && failed
    && statusText === "失败"
    && exitCode !== undefined
    && exitCode !== null
    && exitCode !== 0;

  return (
    <details className="tool-item tool-item--shell" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="tool-icon tool-icon--terminal" aria-hidden="true">
          <ShellIcon />
        </span>
        <span>{shellSummary(running ? "in_progress" : item.status, command)}</span>
      </summary>
      <div className="shell-result">
        <div className="shell-command-zone">
          <div className="shell-command-header">
            <p className="shell-label">Shell</p>
            {command && <ShellCopyButton command={command} />}
          </div>
          <pre className="shell-command"><span aria-hidden="true">$ </span>{command}</pre>
        </div>
        {outputSegments.map((segment, index) => (
          <pre
            className={`shell-output${segment.source === "stderr" ? " shell-output--error" : ""}`}
            key={`${segment.source}-${index}`}
          >
            {segment.content}
          </pre>
        ))}
        {gateRejected && <p className="shell-error-code">未执行：与尚未确认的操作冲突</p>}
        {showErrorCode && <p className="shell-error-code">失败 · {code}</p>}
        {showErrorSummary && <p className="shell-error-summary">{summary}</p>}
        {truncated && (
          <p className="shell-diagnostic">
            输出已截断{truncationReason ? ` · ${truncationReason}` : ""}
          </p>
        )}
        {reconciliationRequired && !gateRejected && (
          <p className="shell-diagnostic shell-diagnostic--warning">执行结果尚未确认</p>
        )}
        {!hasOutput && running && <p className="shell-empty">尚未输出</p>}
        {!hasOutput && !running && !pendingVerification && (success || (!showErrorCode && !showErrorSummary)) && <p className="shell-empty">无输出</p>}
        <p className={`shell-status shell-status--${statusTone}`}>
          {showExitCode && (
            <span className="shell-exit-code-pill">
              退出码 {exitCode}
            </span>
          )}
          <span>{statusText}</span>
        </p>
      </div>
    </details>
  );
}

function PlanResponseItem({
  item,
  onOpenPlan,
}: {
  item: Item;
  onOpenPlan?: (() => void) | undefined;
}) {
  const toolCall = item.toolCall;
  if (!toolCall) return null;
  const args = parseObject(toolCall.argumentsJson);
  const resultObj = parseObject(toolCall.resultJson);
  const dataObj = objectField(resultObj, "data");
  const planTitle = stringField(args, "title") || stringField(dataObj, "title") || "计划";

  if (item.status === "in_progress") {
    return (
      <div className="feed-item feed-item--assistant feed-item--plan">
        <div className="tool-item tool-item--plan-running">
          <span className="tool-icon tool-icon--plan" aria-hidden="true">
            <LightbulbIcon />
          </span>
          <span>正在制定计划…</span>
        </div>
      </div>
    );
  }

  if (item.status !== "completed" || toolCall.status !== "completed" || resultObj.outcome !== "success") {
    return <ToolItem item={item} toolCall={toolCall} />;
  }

  return (
    <div className="feed-item feed-item--assistant feed-item--plan">
      <div
        className="tool-item tool-item--plan-card"
        role="button"
        tabIndex={0}
        onClick={() => onOpenPlan?.()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onOpenPlan?.();
          }
        }}
      >
        <span className="tool-icon tool-icon--plan" aria-hidden="true">
          <LightbulbIcon />
        </span>
        <span className="tool-plan-title">{planTitle}</span>
        <span className="tool-plan-action">查看计划 →</span>
      </div>
    </div>
  );
}

function formatFileSize(bytes: number | undefined): string {
  if (bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatMime(mime: string): string {
  if (!mime) return "";
  if (mime === "image/jpeg") return "JPEG";
  if (mime === "image/png") return "PNG";
  if (mime === "image/webp") return "WebP";
  if (mime === "image/gif") return "GIF";
  if (mime === "image/svg+xml") return "SVG";
  return mime.replace(/^image\//i, "").toUpperCase();
}

function ViewImageViewer({
  toolCall,
  onOpenFile,
  isError,
  errorSummary,
}: {
  toolCall: ToolCall;
  onOpenFile?: ((path: string) => void) | undefined;
  isError: boolean;
  errorSummary?: string | undefined;
}) {
  const actions = useArtifacts();
  const [lightboxOpen, setLightboxOpen] = useState(false);

  useEffect(() => {
    if (!lightboxOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setLightboxOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [lightboxOpen]);

  const args = parseObject(toolCall.argumentsJson);
  const result = parseObject(toolCall.resultJson);
  const data = objectField(result, "data");
  const rawPath = stringField(args, "path") || stringField(data, "path");
  const mime = stringField(data, "mime");
  const size = numberField(data, "size");
  const displayName = rawPath ? pathBasename(rawPath) : "图像";

  const targetPath = actions?.executionRoot && rawPath
    ? artifactPath(rawPath, actions.executionRoot, undefined, true)
    : undefined;
  const { url: previewUrl, error: previewError } = usePreviewUrl(isError ? undefined : targetPath);

  if (isError) {
    return (
      <div className="tool-image-error">
        {errorSummary && <p className="tool-summary--error">{errorSummary}</p>}
        {rawPath && (
          <div className="tool-file-entry">
            {onOpenFile ? (
              <button
                type="button"
                className="tool-file-link"
                title={`打开当前文件：${rawPath}`}
                onClick={() => onOpenFile(rawPath)}
              >
                {rawPath}
              </button>
            ) : (
              <span className="tool-file-link" title={rawPath}>{rawPath}</span>
            )}
          </div>
        )}
      </div>
    );
  }

  const metaParts = [formatMime(mime), formatFileSize(size)].filter(Boolean);
  const metaText = metaParts.join(" · ");
  const absolutePath = stringField(data, "path")
    || (actions?.executionRoot && rawPath && !rawPath.startsWith("/")
      ? `${actions.executionRoot.replace(/\/+$/, "")}/${rawPath.replace(/^\.?\/+/, "")}`
      : rawPath);
  const tooltipDetails = [absolutePath, metaText].filter(Boolean).join(" · ");
  const imageTooltip = tooltipDetails || `预览 ${displayName}`;

  return (
    <div className="tool-image-container" title={imageTooltip}>
      <div className="tool-image-preview-card">
        {previewUrl ? (
          <button
            type="button"
            className="tool-image-thumbnail-btn"
            onClick={() => setLightboxOpen(true)}
            title={imageTooltip}
            aria-label={`预览 ${displayName}`}
          >
            <img src={previewUrl} alt={displayName} loading="lazy" />
          </button>
        ) : previewError || !targetPath ? (
          <p className="tool-image-empty" role="status">暂无预览</p>
        ) : (
          <div className="tool-image-loading">
            <span>正在加载图像…</span>
          </div>
        )}
      </div>

      {lightboxOpen && typeof document !== "undefined" && createPortal(
        <div
          className="artifact-image-preview"
          role="dialog"
          aria-modal="true"
          aria-label={`${displayName} 图片预览`}
          onClick={(event) => {
            if (event.target === event.currentTarget) setLightboxOpen(false);
          }}
        >
          <button
            type="button"
            className="artifact-image-preview__close"
            aria-label="关闭图片预览"
            autoFocus
            onClick={() => setLightboxOpen(false)}
          >
            <CloseIcon />
          </button>
          {previewUrl ? (
            <img src={previewUrl} alt={displayName} />
          ) : (
            <p role="status">暂无预览</p>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}

function WebSearchResultsViewer({
  toolCall,
  item,
  isError,
  errorSummary,
}: {
  toolCall: ToolCall;
  item: Item;
  isError: boolean;
  errorSummary?: string | undefined;
}) {
  const actions = useArtifacts();
  if (isError) {
    return <p className="tool-summary--error">{errorSummary || "搜索失败"}</p>;
  }
  if (item.status === "in_progress") {
    return <p className="tool-web-search-empty">正在搜索中…</p>;
  }

  const resultObj = parseObject(toolCall.resultJson);
  const dataObj = objectField(resultObj, "data");
  const rawResults = Array.isArray(dataObj.results)
    ? dataObj.results
    : Array.isArray(resultObj.results)
    ? resultObj.results
    : [];
  const results = rawResults.filter((r): r is Record<string, unknown> => Boolean(r) && typeof r === "object");

  if (results.length === 0) {
    return <p className="tool-web-search-empty">未找到相关网页结果</p>;
  }

  return (
    <div className="tool-web-search-results" role="list">
      {results.map((hit, index) => {
        const excerpt = stringField(hit, "excerpt") || stringField(hit, "snippet") || stringField(hit, "title") || stringField(hit, "url");
        const url = stringField(hit, "url");
        const fullText = excerpt || stringField(hit, "title");
        const canOpen = Boolean(url && actions?.openBrowser);

        return (
          <div
            key={index}
            className={`tool-web-search-item${canOpen ? " tool-web-search-item--clickable" : ""}`}
            role="listitem"
            title={fullText}
            onClick={canOpen ? () => actions?.openBrowser?.(url) : undefined}
          >
            <span className="tool-web-search-bullet" aria-hidden="true">•</span>
            <span className="tool-web-search-excerpt">{excerpt}</span>
          </div>
        );
      })}
    </div>
  );
}

function ToolSearchResultsViewer({
  toolCall,
  item,
  isError,
  errorSummary,
}: {
  toolCall: ToolCall;
  item: Item;
  isError: boolean;
  errorSummary?: string | undefined;
}) {
  if (isError) {
    return <p className="tool-summary--error">{errorSummary || "搜索失败"}</p>;
  }
  if (item.status === "in_progress") {
    return <p className="tool-search-empty">正在搜索工具…</p>;
  }

  const resultObj = parseObject(toolCall.resultJson);
  const dataObj = objectField(resultObj, "data");
  const rawHits = Array.isArray(dataObj.hits)
    ? dataObj.hits
    : Array.isArray(resultObj.hits)
    ? resultObj.hits
    : [];
  const hits = rawHits.filter((h): h is Record<string, unknown> => Boolean(h) && typeof h === "object");

  if (hits.length === 0) {
    return <p className="tool-search-empty">未搜索到相关工具</p>;
  }

  return (
    <div className="tool-search-results" role="list">
      {hits.map((hit, index) => {
        const name = stringField(hit, "name");
        const description = stringField(hit, "description");
        return (
          <div
            key={index}
            className="tool-search-item"
            role="listitem"
            title={description || name}
          >
            <span className="tool-search-bullet" aria-hidden="true">•</span>
            <span className="tool-search-name">{name}</span>
            {description && <span className="tool-search-desc">{description}</span>}
          </div>
        );
      })}
    </div>
  );
}

function SkillReadToolItem({
  item,
  toolCall,
  agents,
}: {
  item: Item;
  toolCall: ToolCall;
  agents?: AgentSummary[] | undefined;
}) {
  const [previewSkill, setPreviewSkill] = useState<{ qualifiedId: string; name: string; description?: string | undefined } | null>(null);
  const { qualifiedId, name: skillName, description: skillDesc } = extractSkillInfo(toolCall);
  const running = item.status === "in_progress";
  const resultObj = parseObject(toolCall.resultJson);
  const isError = Boolean(
    resultObj.outcome === "error"
    || (typeof resultObj.code === "string" && resultObj.code && resultObj.code !== "ok")
    || item.status === "failed"
    || item.status === "declined"
    || item.status === "canceled"
    || toolCall.status === "failed"
    || toolCall.status === "canceled",
  );

  if (isReconciliationGate(resultObj)) {
    const summaryText = toolSummary(toolCall, item.status, agents);
    return (
      <div className="tool-item tool-item--read-done" title={summaryText}>
        <span className="tool-icon" aria-hidden="true">
          <SkillIcon />
        </span>
        <span>{summaryText}</span>
      </div>
    );
  }

  const errSummary = safeToolSummary(toolCall.resultJson, item.status);
  const titleText = isError && errSummary ? errSummary : undefined;
  const isResource = toolCall.toolName === "skill_read_resource";
  const suffix = isResource ? "整个技能包" : "技能 SKILL.md";
  const actionPrefix = running
    ? "正在读取"
    : (item.status === "completed" ? "已读取" : `${statusLabel(item.status)} 读取`);

  const handleOpenPreview = () => {
    if (!qualifiedId && !skillName) return;
    setPreviewSkill({
      qualifiedId: qualifiedId || skillName,
      name: skillName || qualifiedId,
      description: skillDesc,
    });
  };

  return (
    <>
      <div className="tool-item tool-item--read-done" title={titleText}>
        <span className="tool-icon" aria-hidden="true">
          <SkillIcon />
        </span>
        <span>
          {actionPrefix}
          {skillName ? (
            <button
              type="button"
              className="tool-file-link"
              title={`查看技能详情：${skillName}`}
              onClick={handleOpenPreview}
            >
              {skillName}
            </button>
          ) : null}
          {suffix}
        </span>
      </div>
      {previewSkill && (
        <SkillDetailDialog
          skill={{
            qualifiedId: previewSkill.qualifiedId,
            name: previewSkill.name,
            description: previewSkill.description,
            enabled: true,
            available: true,
          }}
          readOnly
          onClose={() => setPreviewSkill(null)}
        />
      )}
    </>
  );
}

function ToolItem({
  item,
  toolCall,
  onOpenFile,
  agents,
  onOpenSubagent,
}: {
  item: Item;
  toolCall: ToolCall;
  onOpenFile?: ((path: string) => void) | undefined;
  agents?: AgentSummary[] | undefined;
  onOpenSubagent?: ((target?: { id?: string | undefined; taskName?: string | undefined }) => void) | undefined;
}) {
  const [open, setOpen] = useState(false);

  if (isSubagentTool(toolCall.toolName)) {
    const { id: agentId, name: agentName } = extractSubagentInfo(toolCall, agents);
    const resultObj = parseObject(toolCall.resultJson);
    const isError = Boolean(
      resultObj.outcome === "error"
      || (typeof resultObj.code === "string" && resultObj.code && resultObj.code !== "ok")
      || item.status === "failed"
      || item.status === "declined"
      || item.status === "canceled"
      || toolCall.status === "failed"
      || toolCall.status === "canceled"
      || isReconciliationGate(resultObj),
    );
    const errSummary = safeToolSummary(toolCall.resultJson, item.status);
    const actionLabel = subagentActionLabel(toolCall.toolName, item.status, isError, resultObj);
    const titleText = isError && errSummary ? errSummary : undefined;

    return (
      <div className="tool-item tool-item--read-done" title={titleText}>
        <span className="tool-icon" aria-hidden="true">
          <SubagentToolIcon />
        </span>
        <span>
          {agentName ? (
            <>
              {actionLabel}
              {" · "}
              {onOpenSubagent ? (
                <button
                  type="button"
                  className="tool-file-link"
                  onClick={() => onOpenSubagent({ id: agentId, taskName: agentName })}
                >
                  {agentName}
                </button>
              ) : (
                agentName
              )}
            </>
          ) : (
            onOpenSubagent ? (
              <button
                type="button"
                className="tool-file-link"
                onClick={() => onOpenSubagent()}
              >
                {actionLabel}
              </button>
            ) : (
              actionLabel
            )
          )}
        </span>
      </div>
    );
  }

  const toolResult = parseObject(toolCall.resultJson);
  if (toolCall.toolName === "automation" && toolCall.provenance?.kind === "builtin" && toolCall.provenance.sourceId === "eidos.automation" && toolResult.outcome === "success") {
    const data = toolResult.data && typeof toolResult.data === "object" ? toolResult.data as Record<string, unknown> : {};
    const arguments_ = parseObject(toolCall.argumentsJson);
    return <AutomationToolCard response={data.response} mode={arguments_.mode} schedule={arguments_.schedule} />;
  }
  if (toolCall.toolName === "request_user_input"
    && (item.status === "in_progress" || (item.status === "completed" && toolResult.outcome === "success"))) {
    const args = parseObject(toolCall.argumentsJson);
    const questions = Array.isArray(args.questions)
      ? (args.questions as Array<{ id: string; question: string; options?: Array<{ id: string; label: string }> }>)
      : [];
    const isWaiting = item.status === "in_progress";

    if (isWaiting) {
      return (
        <div className="tool-item tool-item--user-input tool-item--user-input-waiting">
          <div className="tool-user-input-header">
            <span className="tool-icon tool-icon--question" aria-hidden="true">
              <QuestionCircleIcon />
            </span>
            <span className="tool-user-input-title">
              正在询问 {questions.length > 1 ? `${questions.length} 个问题` : "问题"}
            </span>
          </div>
          <div className="tool-user-input-waiting-row">
            <span className="tool-waiting-dots" aria-hidden="true">
              <WaitingDotsIcon />
            </span>
            <span className="tool-waiting-text">正在等待你的回答</span>
          </div>
        </div>
      );
    }

    const resultObj = parseObject(toolCall.resultJson);
    const dataObj = objectField(resultObj, "data");
    const responseObj = objectField(dataObj, "response") || objectField(resultObj, "response");
    const isSkippedAll = responseObj?.status === "skipped";
    const answers = Array.isArray(responseObj?.answers)
      ? (responseObj.answers as Array<{ questionId: string; optionIds?: string[]; text?: string }>)
      : [];

    return (
      <details className="tool-item tool-item--user-input" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>
          <span className="tool-icon tool-icon--question" aria-hidden="true">
            <QuestionCircleIcon />
          </span>
          <span>已询问 {questions.length > 0 ? `${questions.length} 个问题` : "问题"}</span>
        </summary>
        <div className="tool-body tool-body--user-input">
          {questions.map((q) => {
            const answer = answers.find((a) => a.questionId === q.id);
            const optionLabels = (answer?.optionIds ?? [])
              .map((id) => q.options?.find((opt) => opt.id === id)?.label ?? id);
            const answerText = answer?.text?.trim();
            const answerParts = [...optionLabels, ...(answerText ? [answerText] : [])];

            const displayAnswer = isSkippedAll
              ? "已跳过"
              : answerParts.length > 0
                ? answerParts.join("；")
                : "未收到回答";

            return (
              <div key={q.id} className="tool-user-input-entry">
                <p className="tool-user-input-question">{q.question}</p>
                <p className="tool-user-input-answer">{displayAnswer}</p>
              </div>
            );
          })}
        </div>
      </details>
    );
  }

  if (isMemoryTool(toolCall.toolName)) {
    const resultObj = parseObject(toolCall.resultJson);
    const isError = Boolean(
      resultObj.outcome === "error"
      || (typeof resultObj.code === "string" && resultObj.code && resultObj.code !== "ok")
      || item.status === "failed"
      || item.status === "declined"
      || item.status === "canceled"
      || toolCall.status === "failed"
      || toolCall.status === "canceled"
      || isReconciliationGate(resultObj),
    );
    const errSummary = safeToolSummary(toolCall.resultJson, item.status);
    const summaryText = toolSummary(toolCall, item.status, agents);
    const titleText = isError && errSummary ? errSummary : summaryText;

    return (
      <div className="tool-item tool-item--read-done" title={titleText}>
        <span className="tool-icon" aria-hidden="true">
          <MemoryIcon />
        </span>
        <span>{summaryText}</span>
      </div>
    );
  }

  if (isSkillReadTool(toolCall.toolName)) {
    return <SkillReadToolItem item={item} toolCall={toolCall} agents={agents} />;
  }

  if (toolCall.toolName === "read_tool_output") {
    const resultObj = parseObject(toolCall.resultJson);
    const isError = Boolean(
      resultObj.outcome === "error"
      || (typeof resultObj.code === "string" && resultObj.code && resultObj.code !== "ok")
      || item.status === "failed"
      || item.status === "declined"
      || item.status === "canceled"
      || toolCall.status === "failed"
      || toolCall.status === "canceled"
      || isReconciliationGate(resultObj),
    );
    const errSummary = safeToolSummary(toolCall.resultJson, item.status);
    const summaryText = toolSummary(toolCall, item.status, agents);
    const titleText = isError && errSummary ? errSummary : summaryText;

    return (
      <div className="tool-item tool-item--read-done" title={titleText}>
        <span className="tool-icon" aria-hidden="true">
          <ShellIcon />
        </span>
        <span>{summaryText}</span>
      </div>
    );
  }

  const isReadFile = ["read_file", "read_file_range"].includes(toolCall.toolName);
  const isDoneRead = isReadFile && item.status === "completed";

  // Completed read_file: render as a static row — no arrow, no body, filename as clickable link
  if (isDoneRead) {
    const args = parseObject(toolCall.argumentsJson);
    const filePath = stringField(args, "path") || stringField(args, "filePath");
    const displayName = filePath ? pathBasename(filePath) : "文件";
    return (
      <div className="tool-item tool-item--read-done">
        <span className="tool-icon" aria-hidden="true"><FileReadIcon /></span>
        <span>
          已读取{" "}
          {filePath && onOpenFile ? (
            <button
              type="button"
              className="tool-file-link"
              title={filePath}
              onClick={() => onOpenFile(filePath)}
            >
              {displayName}
            </button>
          ) : (
            displayName
          )}
        </span>
      </div>
    );
  }

  const result = parseObject(toolCall.resultJson);
  const isDeclareOutputs = toolCall.toolName === "declare_outputs";
  const isViewImage = toolCall.toolName === "view_image";
  const isWebSearch = toolCall.toolName === "web_search";
  const isToolSearch = toolCall.toolName === "tool_search";
  const isError = Boolean(
    result.outcome === "error"
    || (typeof result.code === "string" && result.code && result.code !== "ok" && !(toolCall.toolName === "request_permissions" && ["permission_granted", "already_granted"].includes(result.code)))
    || item.status === "failed"
    || item.status === "declined"
    || item.status === "canceled"
    || toolCall.status === "failed"
    || toolCall.status === "canceled"
    || isReconciliationGate(result),
  );

  if (toolCall.toolName === "request_permissions" && !isError) {
    const summaryText = toolSummary(toolCall, item.status, agents);
    return (
      <div className="tool-item tool-item--read-done" title={summaryText}>
        <span className="tool-icon" aria-hidden="true">
          {toolIcon(toolCall.toolName)}
        </span>
        <span>{summaryText}</span>
      </div>
    );
  }
  const summary = safeToolSummary(toolCall.resultJson, item.status);
  const isRedundantFileSummary = Boolean(
    summary && (
      /^Success\.\s*Updated the following files:/i.test(summary)
      || summary === "File change committed"
      || (isViewImage && summary === "Image loaded")
    ),
  );
  const showSummary = (!isDeclareOutputs || isError) && !isRedundantFileSummary;
  const filePaths = toolFilePaths(toolCall);
  const patchFileHeader = toolCall.toolName === "apply_patch" && item.kind === "file_change"
    && Boolean(toolCall.changeDiff || toolCall.changeDiffHash);

  return (
    <details className="tool-item" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="tool-icon" aria-hidden="true">{toolIcon(toolCall.toolName)}</span>
        <span>{toolSummary(toolCall, item.status, agents)}</span>
      </summary>
      <div className="tool-body">
        {isViewImage ? (
          <ViewImageViewer
            toolCall={toolCall}
            onOpenFile={onOpenFile}
            isError={isError}
            errorSummary={isError ? summary : undefined}
          />
        ) : isWebSearch ? (
          <WebSearchResultsViewer
            toolCall={toolCall}
            item={item}
            isError={isError}
            errorSummary={isError ? summary : undefined}
          />
        ) : isToolSearch ? (
          <ToolSearchResultsViewer
            toolCall={toolCall}
            item={item}
            isError={isError}
            errorSummary={isError ? summary : undefined}
          />
        ) : (
          <>
            {showSummary && summary && (
              <p className={isError ? "tool-summary--error" : undefined}>{summary}</p>
            )}
            {filePaths.length > 0 && !patchFileHeader && (
              <div className="tool-file-list">
                {filePaths.map((path) => (
                  <div key={path} className="tool-file-entry">
                    {onOpenFile ? (
                      <button
                        type="button"
                        className="tool-file-link"
                        title={`打开当前文件：${path}`}
                        onClick={() => onOpenFile(path)}
                      >
                        {item.kind === "file_change" ? pathBasename(path) : path}
                      </button>
                    ) : (
                      <span className="tool-file-link" title={path}>{item.kind === "file_change" ? pathBasename(path) : path}</span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {item.kind === "file_change" && (toolCall.changeDiff || toolCall.changeDiffHash) && (
              <>
                {toolCall.status !== "completed" && (
                  <p className="feed-label">
                    {toolCall.status === "running" ? "准备或执行中的变更" : "计划变更（可能只完成部分写入）"}
                  </p>
                )}
                {toolCall.changeDiffHash
                  ? <ToolTextView key={toolCall.changeDiffHash} sessionId={item.sessionId} toolCallId={toolCall.id} field="diff" sha256={toolCall.changeDiffHash} totalBytes={toolCall.changeDiffBytes!} onOpenFile={patchFileHeader ? onOpenFile : undefined} />
                  : <DiffViewer diff={toolCall.changeDiff} title="代码变更" onOpenFile={patchFileHeader ? onOpenFile : undefined} />}
              </>
            )}
            {toolCall.resultHash && <ToolTextView key={toolCall.resultHash} sessionId={item.sessionId} toolCallId={toolCall.id} field="result" sha256={toolCall.resultHash} totalBytes={toolCall.resultBytes!} />}
            {toolCall.provenance?.kind === "mcp" && (
              <p className="tool-provenance">
                Plugin {toolCall.provenance.pluginId} · Server {toolCall.provenance.serverId}
                {toolCall.completedAt && ` · ${Math.max(0, toolCall.completedAt - toolCall.startedAt)}ms`}
              </p>
            )}
          </>
        )}
      </div>
    </details>
  );
}

function ProcessLabel({ run }: { run: Run }) {
  const now = useCurrentTime(!TERMINAL_RUN_STATUSES.has(run.status));
  const duration = Math.max(0, (run.completedAt ?? now) - (run.startedAt ?? run.createdAt));
  const prefix = TERMINAL_RUN_STATUSES.has(run.status)
    ? "已处理"
    : "正在处理";
  return <span>{prefix} {formatDuration(duration)}</span>;
}

function RunNotice({ run }: { run: Run }) {
  if (run.status === "succeeded") return null;
  const presentation = terminalRunPresentation(run);
  const active = presentation ?? activeRunPresentation(run);
  if (!active || ["queued", "running", "finalizing"].includes(run.status)) return null;
  return (
    <p className={`run-notice run-notice--${active.tone}`} role={active.tone === "error" ? "alert" : "status"}>
      {active.label}
    </p>
  );
}

function groupItemsByRun(items: Item[]): Array<{ runId: string; items: Item[] }> {
  const groups: Array<{ runId: string; items: Item[] }> = [];
  const byRun = new Map<string, Item[]>();
  for (const item of items) {
    let group = byRun.get(item.runId);
    if (!group) {
      group = [];
      byRun.set(item.runId, group);
      groups.push({ runId: item.runId, items: group });
    }
    group.push(item);
  }
  return groups;
}

function splitRunIntoSegments(items: Item[], activities: readonly ContextCompactionActivity[] = []): Segment[] {
  const sourceSegments: Item[][] = [];
  for (const item of [...items].sort((a, b) => a.ordinal - b.ordinal)) {
    if ((item.kind === "user_message" && item.ordinal <= 1) || sourceSegments.length === 0) {
      sourceSegments.push([]);
    }
    const current = sourceSegments.at(-1);
    if (current) current.push(item);
  }
  return sourceSegments.map((segmentItems, index) => {
    const user = segmentItems.find((item) => item.kind === "user_message" && item.ordinal <= 1);
    const body = segmentItems.filter((item) => item !== user);
    const nextUserAt = sourceSegments[index + 1]?.[0]?.createdAt ?? Infinity;
    const compactions = activities.filter((activity) => activity.startedAt >= (user?.createdAt ?? 0) && activity.startedAt < nextUserAt);
    const lastCompactionAt = Math.max(-Infinity, ...compactions.map((activity) => activity.startedAt));
    const processTools = body.filter(
      (item) => item.kind !== "assistant_message" && item.toolCall?.toolName !== "write_plan",
    );
    if (processTools.length === 0 && compactions.length === 0) {
      return {
        user,
        process: [],
        response: body.filter(
          (item) => item.kind === "assistant_message" || item.toolCall?.toolName === "write_plan",
        ),
      };
    }
    const lastProcessToolOrdinal = Math.max(...processTools.map((item) => item.ordinal));
    const inProcess = (item: Item) => item.kind !== "assistant_message" || item.ordinal <= lastProcessToolOrdinal || item.createdAt <= lastCompactionAt;
    const process: ProcessEntry[] = body.filter((item) => item.toolCall?.toolName !== "write_plan" && inProcess(item))
      .map((item) => ({ kind: "item", item }));
    for (const activity of [...compactions].sort((a, b) => a.startedAt - b.startedAt || a.eventId - b.eventId)) {
      const at = process.findIndex((entry) => entry.kind === "item" && entry.item.createdAt > activity.startedAt);
      process.splice(at === -1 ? process.length : at, 0, { kind: "compaction", activity });
    }
    return {
      user,
      process,
      response: body.filter(
        (item) =>
          item.toolCall?.toolName === "write_plan" ||
          (item.kind === "assistant_message" && !inProcess(item)),
      ),
    };
  });
}

function activeRunPresentation(run: Run) {
  switch (run.status) {
    case "waiting_agents": return { label: "等待子任务", tone: "warning" as const };
    case "waiting_input": return { label: "等待回答", tone: "warning" as const };
    case "waiting_approval": return { label: "等待批准", tone: "warning" as const };
    default: return undefined;
  }
}

function shellSummary(status: Item["status"], command: string): string {
  const compact = command.replace(/\s+/g, " ").trim();
  const visible = compact.length > 96 ? `${compact.slice(0, 95)}…` : compact;
  if (status === "in_progress") return `正在运行 ${visible}`;
  if (status === "completed") return `已运行 ${visible}`;
  return `${statusLabel(status)} ${visible}`;
}

function isSubagentTool(name: string): boolean {
  return [
    "spawn_agent",
    "wait_agents",
    "list_agents",
    "stop_agent",
    "send_message",
    "followup_task",
  ].includes(name);
}

function isMemoryTool(name: string): boolean {
  return [
    "memory_search",
    "memory_read",
    "memory_edit",
  ].includes(name);
}

function formatMemorySearchTerms(args: Record<string, unknown>): string {
  const rawList = Array.isArray(args.queries)
    ? args.queries
    : Array.isArray(args.query)
      ? args.query
      : Array.isArray(args.keywords)
        ? args.keywords
        : undefined;

  if (rawList) {
    return rawList
      .filter((term): term is string => typeof term === "string" && Boolean(term.trim()))
      .map((term) => term.trim())
      .join("|");
  }

  const rawString = typeof args.query === "string"
    ? args.query
    : typeof args.queries === "string"
      ? args.queries
      : typeof args.pattern === "string"
        ? args.pattern
        : "";

  if (rawString.trim()) {
    if (rawString.includes("|")) {
      return rawString
        .split("|")
        .map((s) => s.trim())
        .filter(Boolean)
        .join("|");
    }
    return rawString.trim();
  }

  return "";
}

function isSkillReadTool(name: string): boolean {
  return name === "skill_read" || name === "skill_read_resource";
}

function formatPermissionNames(args: Record<string, unknown>, data?: Record<string, unknown>): string {
  const perms = objectField(args, "permissions");
  const dataPerms = data ? objectField(data, "permissions") : {};
  const rawPerms = args.permissions || (data ? data.permissions : undefined);
  const items: string[] = [];

  const networkEnabled = [perms, args, dataPerms, data ?? {}].some(
    source => source.network === true || objectField(source, "network").enabled === true,
  );
  if (networkEnabled) {
    items.push("网络访问");
  }

  const rawFs = Array.isArray(perms.fileSystem)
    ? perms.fileSystem
    : Array.isArray(perms.file_system)
      ? perms.file_system
      : Array.isArray(args.fileSystem)
        ? args.fileSystem
        : Array.isArray(args.file_system)
          ? args.file_system
          : Array.isArray(dataPerms.fileSystem)
            ? dataPerms.fileSystem
            : Array.isArray(dataPerms.file_system)
              ? dataPerms.file_system
              : data && Array.isArray(data.fileSystem)
                ? data.fileSystem
                : data && Array.isArray(data.file_system)
                  ? data.file_system
                  : undefined;

  if (rawFs) {
    const accessMap: Record<string, string> = {
      read: "读取",
      write: "写入",
      execute: "执行",
      deny: "禁止访问",
    };
    for (const entry of rawFs) {
      if (typeof entry === "string" && entry.trim()) {
        items.push(entry.trim());
      } else if (entry && typeof entry === "object" && !Array.isArray(entry)) {
        const obj = entry as Record<string, unknown>;
        const p = stringField(obj, "path").trim();
        const accessKey = stringField(obj, "access").trim();
        const access = accessMap[accessKey] ?? accessKey;
        if (access && p) {
          items.push(`${access} ${p}`);
        } else if (p) {
          items.push(p);
        } else if (access) {
          items.push(access);
        }
      }
    }
  }

  if (items.length === 0) {
    if (typeof rawPerms === "string" && rawPerms.trim()) {
      const trimmed = rawPerms.trim();
      items.push(trimmed === "network" ? "网络访问" : trimmed);
    } else if (Array.isArray(rawPerms)) {
      for (const p of rawPerms) {
        if (typeof p === "string" && p.trim()) {
          items.push(p.trim() === "network" ? "网络访问" : p.trim());
        }
      }
    } else if (typeof args.permission === "string" && args.permission.trim()) {
      const p = args.permission.trim();
      items.push(p === "network" ? "网络访问" : p);
    } else if (data && typeof data.permission === "string" && data.permission.trim()) {
      const p = data.permission.trim();
      items.push(p === "network" ? "网络访问" : p);
    } else if (typeof args.permissionName === "string" && args.permissionName.trim()) {
      items.push(args.permissionName.trim());
    } else if (typeof args.permission_name === "string" && args.permission_name.trim()) {
      items.push(args.permission_name.trim());
    } else if (data && typeof data.permissionName === "string" && data.permissionName.trim()) {
      items.push(data.permissionName.trim());
    } else if (data && typeof data.permission_name === "string" && data.permission_name.trim()) {
      items.push(data.permission_name.trim());
    }
  }

  return items.join("、");
}

function extractSkillInfo(toolCall: ToolCall): { qualifiedId: string; name: string; description?: string | undefined } {
  const args = parseObject(toolCall.argumentsJson);
  const result = parseObject(toolCall.resultJson);
  const data = objectField(result, "data");
  const qualifiedId = stringField(args, "qualifiedId")
    || stringField(result, "qualifiedId")
    || stringField(data, "qualifiedId")
    || stringField(args, "name")
    || "";
  const rawName = stringField(args, "name") || stringField(args, "skillName") || "";
  const name = rawName || (qualifiedId.includes(":") ? qualifiedId.split(":").slice(1).join(":") : qualifiedId);
  const description = stringField(args, "description")
    || stringField(result, "description")
    || stringField(data, "description")
    || undefined;
  return { qualifiedId, name, description };
}

function extractSubagentInfo(
  toolCall: ToolCall,
  agents?: AgentSummary[],
): { id?: string; name?: string } {
  const args = parseObject(toolCall.argumentsJson);
  const result = parseObject(toolCall.resultJson);
  const data = objectField(result, "data");
  const agentObj = objectField(data, "agent") || objectField(result, "agent");

  let id = stringField(agentObj, "id")
    || stringField(args, "agentId")
    || stringField(args, "agent_id");

  let name = stringField(args, "taskName")
    || stringField(args, "task_name")
    || stringField(args, "name")
    || stringField(args, "agentName")
    || stringField(args, "agent_name")
    || stringField(agentObj, "taskName")
    || stringField(agentObj, "task_name");

  if (!id) {
    const agentIds = Array.isArray(args.agentIds)
      ? args.agentIds
      : Array.isArray(args.agent_ids)
        ? args.agent_ids
        : undefined;
    if (agentIds && agentIds.length === 1 && typeof agentIds[0] === "string") {
      id = agentIds[0];
    }
  }

  if (agents && agents.length > 0) {
    if (id && !name) {
      const match = agents.find((a) => a.id === id);
      if (match) name = match.taskName;
    } else if (name && !id) {
      const match = agents.find((a) => a.taskName === name);
      if (match) id = match.id;
    }
  }

  return { id, name };
}

function subagentActionLabel(
  toolName: string,
  status: Item["status"],
  isError: boolean,
  resultObj: Record<string, unknown>,
): string {
  const baseLabels: Record<string, string> = {
    spawn_agent: "创建智能体",
    wait_agents: "等待智能体",
    list_agents: "查询智能体",
    stop_agent: "停止智能体",
    send_message: "发送智能体消息",
    followup_task: "追加智能体任务",
  };
  const base = baseLabels[toolName] ?? toolName;
  if (isReconciliationGate(resultObj)) {
    return `未执行：与尚未确认的操作冲突 ${base}`;
  }
  if (isError && status !== "completed" && status !== "in_progress") {
    return `${statusLabel(status)} ${base}`;
  }
  const runningLabels: Record<string, string> = {
    spawn_agent: "正在创建智能体",
    wait_agents: "正在等待智能体",
    list_agents: "正在查询智能体",
    stop_agent: "正在停止智能体",
    send_message: "正在发送智能体消息",
    followup_task: "正在追加智能体任务",
  };
  return status === "in_progress" ? (runningLabels[toolName] ?? `正在运行 ${toolName}`) : base;
}

function toolSummary(toolCall: ToolCall, status: Item["status"], agents?: AgentSummary[]): string {
  const args = parseObject(toolCall.argumentsJson);
  const taskName = stringField(args, "name") || stringField(objectField(args, "changes"), "name");
  const result = parseObject(toolCall.resultJson);
  const data = objectField(result, "data");
  const rawImagePath = stringField(args, "path") || stringField(args, "filePath") || stringField(data, "path");
  const imageFileName = rawImagePath ? pathBasename(rawImagePath) : "";
  const path = stringField(args, "path") || stringField(args, "filePath");
  let editRawPath = path;
  let patchFileCount = 0;
  if (!editRawPath && toolCall.toolName === "apply_patch") {
    const patchFiles = toolFilePaths(toolCall);
    patchFileCount = patchFiles.length;
    if (patchFiles.length === 1) {
      editRawPath = patchFiles[0]!;
    } else if (patchFiles.length === 0) {
      const patchText = stringField(args, "patch");
      if (patchText) {
        const parsed = parseMultiFileDiff(patchText);
        patchFileCount = parsed.length;
        if (parsed.length === 1 && parsed[0]?.filename) {
          editRawPath = parsed[0].filename;
        }
      }
    }
  }
  const editFileName = editRawPath ? pathBasename(editRawPath) : "";
  const query = stringField(args, "query") || stringField(args, "pattern") || stringField(data, "query");
  const running = status === "in_progress";
  const subagentInfo = isSubagentTool(toolCall.toolName)
    ? extractSubagentInfo(toolCall, agents)
    : undefined;
  const subagentName = subagentInfo?.name;
  const isMemory = isMemoryTool(toolCall.toolName);
  const memoryQuery = isMemory ? formatMemorySearchTerms(args) : "";
  const isSkillRead = isSkillReadTool(toolCall.toolName);
  const skillInfo = isSkillRead ? extractSkillInfo(toolCall) : undefined;
  const skillName = skillInfo?.name || "";
  const isPermissions = toolCall.toolName === "request_permissions";
  const permissionName = isPermissions ? formatPermissionNames(args, data) : "";

  const applyPatchLabel = patchFileCount > 1
    ? (running ? `正在编辑 ${patchFileCount} 个文件` : `已编辑 ${patchFileCount} 个文件`)
    : (running ? `正在编辑 ${editFileName || "文件"}` : `已编辑 ${editFileName || "文件"}`);

  const baseLabels: Record<string, string> = {
    automation: "定时任务",
    web_search: query ? `搜索 ${query}` : "网页搜索",
    tool_search: query ? `搜索工具 [${query}]` : "搜索工具",
    request_permissions: permissionName ? `申请权限 ${permissionName}` : "申请权限",
    spawn_agent: subagentName ? `创建智能体 · ${subagentName}` : "创建智能体",
    wait_agents: subagentName ? `等待智能体 · ${subagentName}` : "等待智能体",
    list_agents: subagentName ? `查询智能体 · ${subagentName}` : "查询智能体",
    stop_agent: subagentName ? `停止智能体 · ${subagentName}` : "停止智能体",
    send_message: subagentName ? `发送智能体消息 · ${subagentName}` : "发送智能体消息",
    followup_task: subagentName ? `追加智能体任务 · ${subagentName}` : "追加智能体任务",
    view_image: imageFileName ? `查看图像 · ${imageFileName}` : "查看图像",
    memory_search: memoryQuery ? `搜索记忆 "${memoryQuery}"` : "搜索记忆",
    memory_read: "读取记忆",
    memory_edit: args.action === "delete" ? "删除记忆" : "保存记忆",
    read_tool_output: "查看工具输出",
    skill_read: skillName ? `${skillName}技能 SKILL.md` : "技能 SKILL.md",
    skill_read_resource: skillName ? `${skillName}整个技能包` : "整个技能包",
  };
  const targetLabel = isMemory
    ? baseLabels[toolCall.toolName]
    : isSkillRead
    ? baseLabels[toolCall.toolName]
    : toolCall.toolName === "web_search"
    ? baseLabels[toolCall.toolName]
    : toolCall.toolName === "tool_search"
    ? baseLabels[toolCall.toolName]
    : toolCall.toolName === "request_permissions"
    ? baseLabels[toolCall.toolName]
    : (path || query || baseLabels[toolCall.toolName] || toolCall.toolName);
  if (isReconciliationGate(result)) {
    return `未执行：与尚未确认的操作冲突 ${targetLabel}`;
  }
  if (!running && status !== "completed") {
    return `${statusLabel(status)} ${targetLabel}`;
  }
  const labels: Record<string, string> = {
    list_files: running ? "正在列出文件" : "已列出文件",
    read_file: running ? `正在读取 ${path || "文件"}` : `已读取 ${path || "文件"}`,
    read_file_range: running ? `正在读取 ${path || "文件"}` : `已读取 ${path || "文件"}`,
    view_image: running
      ? (imageFileName ? `正在查看图像 · ${imageFileName}` : "正在查看图像")
      : (imageFileName ? `已查看图像 · ${imageFileName}` : "已查看图像"),
    search_text: running ? `正在搜索 ${query || "文本"}` : `已搜索 ${query || "文本"}`,
    web_search: running ? `正在搜索 ${query || "网页"}` : `已搜索 ${query || "网页"}`,
    tool_search: running
      ? (query ? `正在搜索工具 [${query}]` : "正在搜索工具")
      : (query ? `已搜索工具 [${query}]` : "已搜索工具"),
    write_file: running ? `正在编辑 ${editFileName || "文件"}` : `已编辑 ${editFileName || "文件"}`,
    apply_patch: applyPatchLabel,
    delete_file: running ? `正在删除 ${editFileName || "文件"}` : `已删除 ${editFileName || "文件"}`,
    declare_outputs: running ? "正在声明产物" : result.outcome === "success" ? "已声明产物" : "产物声明未完成",
    read_tool_output: "正在查看工具输出",
    skill_read: running
      ? (skillName ? `正在读取${skillName}技能 SKILL.md` : "正在读取技能 SKILL.md")
      : (skillName ? `已读取${skillName}技能 SKILL.md` : "已读取技能 SKILL.md"),
    skill_read_resource: running
      ? (skillName ? `正在读取${skillName}整个技能包` : "正在读取整个技能包")
      : (skillName ? `已读取${skillName}整个技能包` : "已读取整个技能包"),
    memory_search: running
      ? (memoryQuery ? `正在搜索记忆 "${memoryQuery}"` : "正在搜索记忆")
      : (memoryQuery ? `已搜索记忆 "${memoryQuery}"` : "已搜索记忆"),
    memory_read: running ? "正在读取记忆" : "已读取记忆",
    memory_edit: args.action === "delete" ? (running ? "正在删除记忆" : "已删除记忆") : (running ? "正在保存记忆" : "已保存记忆"),
    spawn_agent: running
      ? (subagentName ? `正在创建智能体 · ${subagentName}` : "正在创建智能体")
      : (subagentName ? `创建智能体 · ${subagentName}` : "创建智能体"),
    wait_agents: running
      ? (subagentName ? `正在等待智能体 · ${subagentName}` : "正在等待智能体")
      : (subagentName ? `等待智能体 · ${subagentName}` : "等待智能体"),
    list_agents: running
      ? (subagentName ? `正在查询智能体 · ${subagentName}` : "正在查询智能体")
      : (subagentName ? `查询智能体 · ${subagentName}` : "查询智能体"),
    stop_agent: running
      ? (subagentName ? `正在停止智能体 · ${subagentName}` : "正在停止智能体")
      : (subagentName ? `停止智能体 · ${subagentName}` : "停止智能体"),
    send_message: running
      ? (subagentName ? `正在发送智能体消息 · ${subagentName}` : "正在发送智能体消息")
      : (subagentName ? `发送智能体消息 · ${subagentName}` : "发送智能体消息"),
    followup_task: running
      ? (subagentName ? `正在追加智能体任务 · ${subagentName}` : "正在追加智能体任务")
      : (subagentName ? `追加智能体任务 · ${subagentName}` : "追加智能体任务"),
    automation: running
      ? (taskName ? `正在配置定时任务 · ${taskName}` : "正在配置定时任务")
      : (taskName ? `定时任务 · ${taskName}` : "已运行定时任务"),
    request_permissions: running
      ? (permissionName ? `正在申请权限 ${permissionName}` : "正在申请权限")
      : (permissionName ? `已申请权限 ${permissionName}` : "已申请权限"),
  };
  return labels[toolCall.toolName] ?? `${running ? "正在运行" : "已运行"} ${toolCall.toolName}`;
}

function FileReadIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 2h5.5L13 5.5V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
      <path d="M9.5 2v3.5H13" />
      <path d="M6 8.5h4" />
      <path d="M6 11.5h3" />
    </svg>
  );
}

function FileListIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 2.5h10a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z" />
      <path d="M5.5 6h5" />
      <path d="M5.5 8.5h5" />
      <path d="M5.5 11h3" />
    </svg>
  );
}

function FileWriteIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.5 2.5l3 3L5 14H2v-3L10.5 2.5z" />
      <path d="M9 4l3 3" />
    </svg>
  );
}

function FileDeleteIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.5 4h11" />
      <path d="M5.5 4V2.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V4" />
      <path d="M4 4v9a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6.75" cy="6.75" r="4.25" />
      <path d="M10 10l3.75 3.75" />
    </svg>
  );
}

function DeclareOutputsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2L13.5 5L8 8L2.5 5Z" fill="currentColor" fillOpacity=".12" />
      <path d="M8 2L13.5 5V11L8 14L2.5 11V5Z" />
      <path d="M8 8V14" />
      <path d="M5.25 3.5L10.75 6.5" />
    </svg>
  );
}

function ShellIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3.5 4.5L7 8l-3.5 3.5" />
      <path d="M8.5 11.5h4" />
    </svg>
  );
}

function McpIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5.5 2v3M10.5 2v3" />
      <path d="M3.5 5h9v3.5a4.5 4.5 0 0 1-9 0V5z" />
      <path d="M8 13v1.5" />
    </svg>
  );
}

function ImageIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="12" height="12" rx="2" />
      <circle cx="5.5" cy="5.5" r="1.25" />
      <path d="m14 11-3.2-3.2a1.4 1.4 0 0 0-1.8 0L3 13.8" />
    </svg>
  );
}

function WebSearchIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
      <path d="M2 12h20" />
    </svg>
  );
}

function ShieldQuestionIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="M9.1 9a3 3 0 0 1 5.82 1c0 2-3 3-3 3" />
      <path d="M12 17h.01" />
    </svg>
  );
}

function DefaultToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2L14 8L8 14L2 8Z" />
    </svg>
  );
}

function toolIcon(name: string): ReactNode {
  if (name === "automation") return <AutomationsIcon />;
  if (name === "web_search") return <WebSearchIcon />;
  if (name === "declare_outputs") return <DeclareOutputsIcon />;
  if (name === "request_permissions") return <ShieldQuestionIcon />;
  if (name === "run_shell" || name === "read_tool_output") return <ShellIcon />;
  if (name === "list_files") return <FileListIcon />;
  if (["read_file", "read_file_range"].includes(name)) return <FileReadIcon />;
  if (name === "view_image") return <ImageIcon />;
  if (isMemoryTool(name)) return <MemoryIcon />;
  if (["search_text", "tool_search"].includes(name)) return <SearchIcon />;
  if (["write_file", "apply_patch"].includes(name)) return <FileWriteIcon />;
  if (name === "delete_file") return <FileDeleteIcon />;
  if (["spawn_agent", "wait_agents", "list_agents", "stop_agent", "send_message", "followup_task"].includes(name)) {
    return <SubagentToolIcon />;
  }
  if (name.startsWith("skill_")) return <SkillIcon />;
  if (name.startsWith("mcp") || name.includes("__")) return <McpIcon />;
  return <DefaultToolIcon />;
}

function statusLabel(status: Item["status"]): string {
  return ({ in_progress: "运行中", completed: "成功", failed: "失败", declined: "已拒绝", canceled: "已取消" } as const)[status];
}

function safeToolSummary(value: string | undefined, status: Item["status"]): string {
  const parsed = parseObject(value);
  if (isReconciliationGate(parsed)) return "未执行：与尚未确认的操作冲突";
  return stringField(parsed, "summary") || stringField(parsed, "code") || statusLabel(status);
}

function isReconciliationGate(result: Record<string, unknown>): boolean {
  const code = stringField(result, "code").toUpperCase();
  return code === "RECONCILIATION_REQUIRED" || code === "TOOL_RECONCILIATION_REQUIRED"
    || code.endsWith("_RECONCILIATION_REQUIRED");
}

function parseObject(value: string | undefined): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function objectField(source: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = source[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringField(source: Record<string, unknown>, key: string): string {
  return typeof source[key] === "string" ? source[key] : "";
}

function stringArrayField(source: Record<string, unknown>, key: string): string[] {
  return Array.isArray(source[key])
    ? source[key].filter((value): value is string => typeof value === "string")
    : [];
}

function booleanField(source: Record<string, unknown>, key: string): boolean {
  return source[key] === true;
}

function numberField(source: Record<string, unknown>, key: string): number | undefined {
  return typeof source[key] === "number" ? source[key] : undefined;
}

function formatDuration(durationMs: number): string {
  const seconds = Math.max(0, Math.floor(durationMs / 1_000));
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function useCurrentTime(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [live]);
  return now;
}

function pathBasename(p: string): string {
  return p.split(/[/\\]/).filter(Boolean).at(-1) ?? p;
}

function QuestionCircleIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path
        d="M14.3726 8.00049C14.3726 4.48132 11.5196 1.6285 8.00049 1.62842C4.48127 1.62842 1.62842 4.48127 1.62842 8.00049C1.6285 11.5196 4.48132 14.3726 8.00049 14.3726C11.5196 14.3725 14.3725 11.5196 14.3726 8.00049ZM15.772 8.00049C15.7719 12.2928 12.2928 15.7719 8.00049 15.772C3.70812 15.772 0.22811 12.2928 0.228027 8.00049C0.228027 3.70807 3.70807 0.228027 8.00049 0.228027C12.2928 0.22811 15.772 3.70812 15.772 8.00049Z"
        fill="currentColor"
      />
      <path
        d="M7.06369 9.92245C7.06369 9.24781 7.23342 8.39641 7.91037 7.82675C8.32682 7.47633 8.87011 7.16969 9.14572 6.98105C9.47422 6.7562 9.62589 6.58962 9.69553 6.38828C9.80348 6.07588 9.7503 5.72497 9.54221 5.44882C9.34217 5.18345 8.95897 4.94003 8.32248 4.94003C6.85369 4.94006 6.25143 5.84986 6.25119 6.61679H4.8508C4.85104 5.02826 6.1298 3.53968 8.32248 3.53964C9.34633 3.53964 10.1659 3.95013 10.6604 4.60605C11.1465 5.25107 11.2796 6.08921 11.0178 6.84628C10.7986 7.47967 10.34 7.86026 9.93674 8.13632C9.48042 8.44865 9.1697 8.59682 8.81174 8.89804C8.59398 9.08128 8.46408 9.42776 8.46408 9.92245V10.0064H7.06369V9.92245Z"
        fill="currentColor"
      />
      <path
        d="M8.45126 10.7892V12.3556H7.05087V10.7892H8.45126Z"
        fill="currentColor"
      />
    </svg>
  );
}

function WaitingDotsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
      <circle cx="5" cy="5.5" r="1.3" />
      <circle cx="5" cy="10.5" r="1.3" />
      <circle cx="11" cy="5.5" r="1.3" />
      <circle cx="11" cy="10.5" r="1.3" />
    </svg>
  );
}

function LightbulbIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 2a4.5 4.5 0 0 0-3 7.8V11.5a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1V9.8A4.5 4.5 0 0 0 8 2z" />
      <path d="M6.5 14h3" />
    </svg>
  );
}
