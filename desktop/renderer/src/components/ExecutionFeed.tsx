import { InputReferenceCards } from "./InputContext.js";
import { ToolTextView } from "./ToolTextView.js";
import { toolFilePaths } from "./ResultFiles.js";
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import stripAnsi from "strip-ansi";

import type {
  ApprovalRequest,
  Item,
  ModelOption,
  ResponseActionState,
  ResponseFeedbackValue,
  Run,
  StepResolutionReview,
  ToolCall,
} from "../contracts.js";
import { terminalRunPresentation } from "../session-state.js";
import { Button } from "./Button.js";
import { MarkdownContent } from "./MarkdownContent.js";
import { ApprovalRecoveryBanner } from "./ApprovalRecoveryBanner.js";
import { DropdownMenu } from "./DropdownMenu.js";
import { TurnResults } from "./TurnResults.js";


type FeedbackHandler = (
  itemId: string,
  feedback: ResponseFeedbackValue | null,
) => Promise<void>;
type RegenerateHandler = (run: Run) => Promise<void>;
type EditResendHandler = (run: Run, editedInput: string, references?: string[]) => Promise<boolean | void>;

interface Props {
  items: Item[];
  resultItems?: Item[];
  projectless?: boolean;
  runs: Run[];
  models?: ModelOption[];
  workspaceRoot?: string;
  responseActionState?: ResponseActionState;
  pendingFeedbackItemIds?: ReadonlySet<string>;
  revisionSubmitting?: boolean;
  stepResolutions?: StepResolutionReview[] | undefined;
  approvals: ApprovalRequest[];
  respondingApprovalIds?: ReadonlySet<string> | undefined;
  respondingKindByApprovalId?: Readonly<Record<string, "approve" | "reject">> | undefined;
  expiredApprovalIds?: ReadonlySet<string> | undefined;
  errorsByApprovalId?: Readonly<Record<string, string>> | undefined;
  approvalLoadError?: string | undefined;
  loadingPendingApprovals?: boolean | undefined;
  onRetryLoadPending?: (() => void) | undefined;
  onApprove: (request: ApprovalRequest) => void;
  onReject: (request: ApprovalRequest) => void;
  onFeedback?: FeedbackHandler;
  onRegenerate?: RegenerateHandler;
  onEditResend?: EditResendHandler;
  onOpenFile?: ((path: string) => void) | undefined;
  onOpenPlan?: (() => void) | undefined;
}

interface Segment {
  user: Item | undefined;
  process: Item[];
  response: Item[];
}

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


export function ExecutionFeed({
  items,
  resultItems = items,
  projectless = false,
  runs,
  models = [],
  workspaceRoot = "",
  responseActionState = EMPTY_RESPONSE_ACTION_STATE,
  pendingFeedbackItemIds = EMPTY_PENDING_FEEDBACK,
  revisionSubmitting = false,
  stepResolutions = [],
  approvals,
  respondingApprovalIds,
  respondingKindByApprovalId,
  expiredApprovalIds,
  errorsByApprovalId,
  approvalLoadError,
  loadingPendingApprovals,
  onRetryLoadPending,
  onApprove,
  onReject,
  onFeedback = NOOP_FEEDBACK,
  onRegenerate = NOOP_REGENERATE,
  onEditResend = NOOP_EDIT_RESEND,
  onOpenFile,
  onOpenPlan,
}: Props) {
  const feedRef = useRef<HTMLElement>(null);
  const isAtBottomRef = useRef(true);
  const followRafRef = useRef<number | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  const handleScroll = useCallback((event: React.UIEvent<HTMLElement>) => {
    const bottom = isFeedAtBottom(event.currentTarget);
    isAtBottomRef.current = bottom;
    setAtBottom(bottom);
  }, []);

  useEffect(() => {
    return () => {
      if (followRafRef.current !== null) {
        cancelAnimationFrame(followRafRef.current);
        followRafRef.current = null;
      }
    };
  }, []);

  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (!feed || !isAtBottomRef.current) return;
    if (followRafRef.current !== null) return;
    followRafRef.current = requestAnimationFrame(() => {
      followRafRef.current = null;
      const target = feedRef.current;
      if (!target || !isAtBottomRef.current) return;
      target.scrollTop = target.scrollHeight;
    });
  }, [items, runs, responseActionState.revisions]);

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

  if (items.length === 0) {
    return (
      <div className="feed-empty" role="status">
        {approvalLoadError && onRetryLoadPending && (
          <ApprovalRecoveryBanner
            error={approvalLoadError}
            loading={loadingPendingApprovals}
            onRetry={onRetryLoadPending}
          />
        )}
        <p>这个 Session 还没有执行记录。</p>
      </div>
    );
  }

  const runsById = new Map(runs.map((run) => [run.id, run]));
  const itemGroups = groupItemsByRun(items).filter(({ runId }) => !supersededRunIds.has(runId));

  return (
    <div className="feed-shell">
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
        aria-label="Execution Feed"
        aria-live="polite"
        onScroll={handleScroll}
      >
        {itemGroups.map(({ runId, items: runItems }) => {
          const run = runsById.get(runId);
          if (!run) return null;
          const segments = splitRunIntoSegments(runItems);
          const canReviseRun = latestVisibleRun?.id === run.id
            && TERMINAL_RUN_STATUSES.has(run.status)
            && !hasActiveRun
            && !revisionSubmitting;
          const modelName = models.find((model) => model.id === run.modelId)?.name ?? run.modelId;
          return (
            <Fragment key={runId}>
              {segments.map((segment, index) => (
                <RunSegment
                  key={`${runId}:${segment.user?.id ?? index}`}
                  segment={segment}
                  run={run}
                  resultItems={resultItems}
                  projectless={projectless}
                  modelName={modelName}
                  workspaceRoot={workspaceRoot}
                  isLast={index === segments.length - 1}
                  canReviseRun={canReviseRun}
                  atBottom={atBottom}
                  feedbackByItemId={feedbackByItemId}
                  pendingFeedbackItemIds={pendingFeedbackItemIds}
                  revisionSubmitting={revisionSubmitting}
                  approvals={approvals}
                  respondingApprovalIds={respondingApprovalIds}
                  respondingKindByApprovalId={respondingKindByApprovalId}
                  expiredApprovalIds={expiredApprovalIds}
                  errorsByApprovalId={errorsByApprovalId}
                  onApprove={onApprove}
                  onReject={onReject}
                  onFeedback={onFeedback}
                  onRegenerate={onRegenerate}
                  onEditResend={onEditResend}
                  onOpenFile={onOpenFile}
                  onOpenPlan={onOpenPlan}
                />
              ))}
              <RunNotice run={run} />
            </Fragment>
          );
        })}
      </section>
      <button
        className="feed-jump-to-bottom"
        type="button"
        aria-label="滚动到最新内容"
        hidden={atBottom}
        onClick={() => {
          isAtBottomRef.current = true;
          setAtBottom(true);
          feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: "smooth" });
        }}
      >
        <span aria-hidden="true">↓</span>
      </button>
    </div>
  );
}

export function isFeedAtBottom(
  feed: Pick<HTMLElement, "scrollHeight" | "scrollTop" | "clientHeight">,
): boolean {
  return feed.scrollHeight - feed.scrollTop - feed.clientHeight <= 16;
}

function RunSegment({
  segment,
  run,
  resultItems,
  projectless,
  modelName,
  workspaceRoot,
  isLast,
  canReviseRun,
  atBottom = true,
  feedbackByItemId,
  pendingFeedbackItemIds,
  revisionSubmitting,
  approvals,
  respondingApprovalIds,
  respondingKindByApprovalId,
  expiredApprovalIds,
  errorsByApprovalId,
  onApprove,
  onReject,
  onFeedback,
  onRegenerate,
  onEditResend,
  onOpenFile,
  onOpenPlan,
}: {
  segment: Segment;
  run: Run;
  resultItems: Item[];
  projectless: boolean;
  modelName: string;
  workspaceRoot?: string | undefined;
  isLast: boolean;
  canReviseRun: boolean;
  atBottom?: boolean | undefined;
  feedbackByItemId: ReadonlyMap<string, ResponseFeedbackValue>;
  pendingFeedbackItemIds: ReadonlySet<string>;
  revisionSubmitting: boolean;
  approvals: ApprovalRequest[];
  respondingApprovalIds?: ReadonlySet<string> | undefined;
  respondingKindByApprovalId?: Readonly<Record<string, "approve" | "reject">> | undefined;
  expiredApprovalIds?: ReadonlySet<string> | undefined;
  errorsByApprovalId?: Readonly<Record<string, string>> | undefined;
  onApprove: Props["onApprove"];
  onReject: Props["onReject"];
  onFeedback: FeedbackHandler;
  onRegenerate: RegenerateHandler;
  onEditResend: EditResendHandler;
  onOpenFile?: ((path: string) => void) | undefined;
  onOpenPlan?: (() => void) | undefined;
}) {
  // Hidden observations still determine which assistant messages are progress.
  const visibleProcess = segment.process.filter((item) => {
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
          canEdit={isLast && canReviseRun}
          revisionSubmitting={revisionSubmitting}
          onEditResend={onEditResend}
        />
      )}
      {visibleProcess.length > 0 && (
        <ProcessGroup
          run={run}
        >
          {visibleProcess.map((item) => (
            <ProcessItem
              key={item.id}
              item={item}
              run={run}
              approval={approvals.find((request) => request.itemId === item.id)}
              respondingApprovalIds={respondingApprovalIds}
              respondingKindByApprovalId={respondingKindByApprovalId}
              expiredApprovalIds={expiredApprovalIds}
              errorsByApprovalId={errorsByApprovalId}
              onApprove={onApprove}
              onReject={onReject}
              onOpenFile={onOpenFile}
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
            atBottom={atBottom}
            feedback={feedbackByItemId.get(item.id)}
            feedbackPending={pendingFeedbackItemIds.has(item.id)}
            canRegenerate={isLast && index === segment.response.length - 1 && canReviseRun}
            isFinal={isLast && index === segment.response.length - 1 && TERMINAL_RUN_STATUSES.has(run.status)}
            showTurnResults={isLast
              && index === segment.response.length - 1
              && TERMINAL_RUN_STATUSES.has(run.status)
              && segment.response.every((responseItem) => responseItem.status !== "in_progress")}
            resultItems={resultItems}
            showTextChanges={true}
            onFeedback={onFeedback}
            onRegenerate={onRegenerate}
          />
        );
      })}
      {isLast
        && segment.response.length === 0
        && TERMINAL_RUN_STATUSES.has(run.status)
        && <TurnResults run={run} items={resultItems} showTextChanges={true} />}
    </>
  );
}

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
  );
}

function UserMessage({
  item,
  run,
  canEdit,
  revisionSubmitting,
  onEditResend,
}: {
  item: Item;
  run: Run;
  canEdit: boolean;
  revisionSubmitting: boolean;
  onEditResend: EditResendHandler;
}) {
  const formattedTime = formatItemTime(item.completedAt ?? item.createdAt);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.content ?? "");
  const [draftReferences, setDraftReferences] = useState(item.references ?? []);

  useEffect(() => {
    if (!editing) { setDraft(item.content ?? ""); setDraftReferences(item.references ?? []); }
  }, [editing, item.content, item.references]);

  async function submitEdit(): Promise<void> {
    const value = draft.trim();
    if ((!value && !draftReferences.length) || revisionSubmitting) return;
    const accepted = await onEditResend(run, value, draftReferences.map((reference) => reference.id));
    if (accepted !== false) setEditing(false);
  }

  return (
    <div className="feed-item feed-item--user">
      <div className="user-message-bubble">
        {(editing ? draftReferences : item.references)?.length ? <InputReferenceCards references={editing ? draftReferences : item.references ?? []} {...(editing ? { onRemove: (id: string) => setDraftReferences((previous) => previous.filter((reference) => reference.id !== id)) } : {})} /> : null}
        {editing ? (
          <div className="user-message-editor">
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
          <p>{item.content}</p>
        )}
      </div>
      {!editing && (
        <div className="feed-item-footer response-footer">
          <div className="response-actions-left">
            {item.content && <CopyButton content={item.content} />}
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
  atBottom = true,
  feedback,
  feedbackPending,
  canRegenerate,
  isFinal,
  showTurnResults,
  resultItems,
  showTextChanges,
  onFeedback,
  onRegenerate,
}: {
  item: Item;
  run: Run;
  modelName: string;
  workspaceRoot?: string | undefined;
  atBottom?: boolean | undefined;
  feedback: ResponseFeedbackValue | undefined;
  feedbackPending: boolean;
  canRegenerate: boolean;
  isFinal: boolean;
  showTurnResults: boolean;
  resultItems: Item[];
  showTextChanges: boolean;
  onFeedback: FeedbackHandler;
  onRegenerate: RegenerateHandler;
}) {
  const contentRef = useRef<HTMLDivElement>(null);
  const formattedTime = formatItemTime(item.completedAt ?? item.createdAt);
  const isStreaming = item.status === "in_progress";

  const canFeedback = item.status === "completed" && Boolean(item.content);

  return (
    <article className="feed-item feed-item--assistant" ref={contentRef}>
      <MarkdownContent content={item.content || ""} isStreaming={isStreaming} />
      {(item.incomplete || item.status === "canceled") && <p role="status">回答未完成</p>}
      {showTurnResults && <TurnResults run={run} items={resultItems} showTextChanges={showTextChanges} />}
      {isFinal && item.content && (
        <div className="feed-item-footer response-footer">
          <div className="response-actions-left">
            <CopyButton content={item.content} />
            <ActionButton
              label={feedback === "up" ? "取消点赞" : "点赞"}
              active={feedback === "up"}
              disabled={!canFeedback || feedbackPending}
              onClick={() => void onFeedback(item.id, feedback === "up" ? null : "up")}
            >
              <ThumbUpIcon />
            </ActionButton>
            <ActionButton
              label={feedback === "down" ? "取消差评" : "差评"}
              active={feedback === "down"}
              disabled={!canFeedback || feedbackPending}
              onClick={() => void onFeedback(item.id, feedback === "down" ? null : "down")}
            >
              <ThumbDownIcon />
            </ActionButton>
            {canRegenerate && (
              <ActionButton label="重新回答" onClick={() => void onRegenerate(run)}>
                <RegenerateIcon />
              </ActionButton>
            )}
            <MoreActionsDropdown
              session={run.sessionId}
              run={run.id}
              item={item.id}
              step={item.modelStepIndex ?? run.modelStepCount ?? 1}
              model={run.modelId}
              workspace={workspaceRoot}
            />
          </div>
          <div className="response-meta">
            <span className="response-model" title={`本次回复模型：${modelName}`}>{modelName}</span>
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
      traceId: run,
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
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`response-action-button${active ? " response-action-button--active" : ""}`}
      disabled={disabled}
      title={label}
      aria-label={label}
      aria-pressed={active || undefined}
      onClick={onClick}
    >
      {children}
    </button>
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
      <path d="M20 7v5h-5" />
      <path d="M19 12a7 7 0 1 1-2-5" />
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
  respondingApprovalIds,
  respondingKindByApprovalId,
  expiredApprovalIds,
  errorsByApprovalId,
  onApprove,
  onReject,
  onOpenFile,
}: {
  item: Item;
  run: Run;
  approval: ApprovalRequest | undefined;
  respondingApprovalIds?: ReadonlySet<string> | undefined;
  respondingKindByApprovalId?: Readonly<Record<string, "approve" | "reject">> | undefined;
  expiredApprovalIds?: ReadonlySet<string> | undefined;
  errorsByApprovalId?: Readonly<Record<string, string>> | undefined;
  onApprove: Props["onApprove"];
  onReject: Props["onReject"];
  onOpenFile?: ((path: string) => void) | undefined;
}) {
  if (item.kind === "assistant_message") {
    if (!item.content) return null;
    return <div className="process-text"><MarkdownContent content={item.content} isStreaming={item.status === "in_progress"} /></div>;
  }
  if (!item.toolCall) return null;

  if (item.toolCall.toolName === "write_stdin") {
    return item.status === "failed" ? <p className="shell-error-summary">命令跟进失败 · {stringField(parseObject(item.toolCall.resultJson), "code") || "暂时无法读取命令状态"}</p> : null;
  }

  const toolItem = item.toolCall.toolName === "run_shell"
    ? <ShellItem item={item} toolCall={item.toolCall} />
    : <ToolItem item={item} toolCall={item.toolCall} onOpenFile={onOpenFile} />;

  if (approval) {
    return <><p className="feed-label">需要批准 · {{ file_change: "文件变更", command_execution: "Shell 命令", network_access: "网络访问", external_tool: "MCP 工具", permission_request: "权限申请" }[approval.kind]} · <span>{approval.summary}</span></p>{toolItem}</>;
  }
  if (item.toolCall.approvalDecision) {
    return <div><p className="feed-label">{run.approvalMode === "auto_review" ? "模型审查 · " : run.approvalMode === "full_access" ? "完全访问 · " : ""}{item.toolCall.approvalDecision === "approve" ? "已批准" : "已拒绝"} · {item.toolCall.toolName}</p>
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
  const running = item.status === "in_progress" || stringField(data, "executionStatus") === "running";
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
        ? "待核验"
        : success
          ? "✓ 成功"
          : failed
            ? "失败"
            : statusLabel(item.status);
  const [open, setOpen] = useState(false);

  return (
    <details className="tool-item tool-item--shell" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="tool-icon tool-icon--terminal" aria-hidden="true">
          <ShellIcon />
        </span>
        <span>{shellSummary(running ? "in_progress" : item.status, command)}</span>
      </summary>
      <div className="shell-result">
        <p className="shell-label">Shell</p>
        <pre className="shell-command"><span aria-hidden="true">$ </span>{command}</pre>
        {outputSegments.map((segment, index) => (
          <pre
            className={`shell-output${segment.source === "stderr" ? " shell-output--error" : ""}`}
            key={`${segment.source}-${index}`}
          >
            {segment.content}
          </pre>
        ))}
        {gateRejected && <p className="shell-error-code">未执行，等待只读核验</p>}
        {!running && !gateRejected && !pendingVerification && !success && code && <p className="shell-error-code">失败 · {code}</p>}
        {!running && !success && !pendingVerification && summary && <p className="shell-error-summary">{summary}</p>}
        {truncated && (
          <p className="shell-diagnostic">
            输出已截断{truncationReason ? ` · ${truncationReason}` : ""}
          </p>
        )}
        {reconciliationRequired && !gateRejected && (
          <p className="shell-diagnostic shell-diagnostic--warning">结果需要只读核验</p>
        )}
        {!hasOutput && running && <p className="shell-empty">尚未输出</p>}
        {!hasOutput && !running && !pendingVerification && (success || (!code && !summary)) && <p className="shell-empty">无输出</p>}
        <p className={`shell-status shell-status--${statusTone}`}>{statusText}</p>
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

function ToolItem({ item, toolCall, onOpenFile }: {
  item: Item;
  toolCall: ToolCall;
  onOpenFile?: ((path: string) => void) | undefined;
}) {
  const [open, setOpen] = useState(item.status === "in_progress");
  useEffect(() => {
    if (item.status !== "in_progress") setOpen(false);
  }, [item.status]);

  const toolResult = parseObject(toolCall.resultJson);
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
  const isError = Boolean(
    result.outcome === "error"
    || (typeof result.code === "string" && result.code && result.code !== "ok")
    || item.status === "failed"
    || item.status === "declined"
    || item.status === "canceled"
    || toolCall.status === "failed"
    || toolCall.status === "canceled"
    || isReconciliationGate(result),
  );
  const showSummary = !isDeclareOutputs || isError;
  const summary = safeToolSummary(toolCall.resultJson, item.status);
  const filePaths = toolFilePaths(toolCall);

  return (
    <details className="tool-item" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="tool-icon" aria-hidden="true">{toolIcon(toolCall.toolName)}</span>
        <span>{toolSummary(toolCall, item.status)}</span>
      </summary>
      <div className="tool-body">
        {showSummary && summary && (
          <p className={isError ? "tool-summary--error" : undefined}>{summary}</p>
        )}
        {filePaths.length > 0 && (
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
                    {path}
                  </button>
                ) : (
                  <span className="tool-file-link" title={path}>{path}</span>
                )}
              </div>
            ))}
          </div>
        )}
        {item.kind === "file_change" && (toolCall.changeDiff || toolCall.changeDiffHash) && (
          <>
            <p className="feed-label">{toolCall.status === "completed" ? "已完成的变更" : toolCall.status === "running" ? "准备或执行中的变更" : "计划变更（可能只完成部分写入）"}</p>
            {toolCall.changeDiffHash
              ? <ToolTextView key={toolCall.changeDiffHash} sessionId={item.sessionId} toolCallId={toolCall.id} field="diff" sha256={toolCall.changeDiffHash} totalBytes={toolCall.changeDiffBytes!} />
              : <pre className="diff-view">{toolCall.changeDiff}</pre>}
          </>
        )}
        {toolCall.resultHash && <ToolTextView key={toolCall.resultHash} sessionId={item.sessionId} toolCallId={toolCall.id} field="result" sha256={toolCall.resultHash} totalBytes={toolCall.resultBytes!} />}
        {toolCall.provenance?.kind === "mcp" && (
          <p className="tool-provenance">
            Plugin {toolCall.provenance.pluginId} · Server {toolCall.provenance.serverId}
            {toolCall.completedAt && ` · ${Math.max(0, toolCall.completedAt - toolCall.startedAt)}ms`}
          </p>
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
  if (run.status === "succeeded" && run.reconciliationRequired !== true) return null;
  const presentation = terminalRunPresentation(run);
  const active = presentation ?? activeRunPresentation(run);
  if (!active || ["queued", "running", "finalizing"].includes(run.status)) return null;
  return (
    <p className={`run-notice run-notice--${active.tone}`} role={active.tone === "error" ? "alert" : "status"}>
      {active.label}
      {run.reconciliationRequired === true
        && "。副作用结果可能存在，下一步必须先只读核验"}
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

function splitRunIntoSegments(items: Item[]): Segment[] {
  const sourceSegments: Item[][] = [];
  for (const item of items) {
    if (item.kind === "user_message" || sourceSegments.length === 0) {
      sourceSegments.push([]);
    }
    const current = sourceSegments.at(-1);
    if (current) current.push(item);
  }
  return sourceSegments.map((segmentItems) => {
    const user = segmentItems.find((item) => item.kind === "user_message");
    const body = segmentItems.filter((item) => item.kind !== "user_message");
    const processTools = body.filter(
      (item) => item.kind !== "assistant_message" && item.toolCall?.toolName !== "write_plan",
    );
    if (processTools.length === 0) {
      return {
        user,
        process: [],
        response: body.filter(
          (item) => item.kind === "assistant_message" || item.toolCall?.toolName === "write_plan",
        ),
      };
    }
    const lastProcessToolOrdinal = Math.max(...processTools.map((item) => item.ordinal));
    return {
      user,
      process: body.filter(
        (item) =>
          item.toolCall?.toolName !== "write_plan" &&
          (item.kind !== "assistant_message" || item.ordinal <= lastProcessToolOrdinal),
      ),
      response: body.filter(
        (item) =>
          item.toolCall?.toolName === "write_plan" ||
          (item.kind === "assistant_message" && item.ordinal > lastProcessToolOrdinal),
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

function toolSummary(toolCall: ToolCall, status: Item["status"]): string {
  const args = parseObject(toolCall.argumentsJson);
  const result = parseObject(toolCall.resultJson);
  const path = stringField(args, "path") || stringField(args, "filePath");
  const query = stringField(args, "query") || stringField(args, "pattern");
  const running = status === "in_progress";
  if (isReconciliationGate(result)) {
    return `未执行，等待只读核验 ${path || query || toolCall.toolName}`;
  }
  if (!running && status !== "completed") {
    return `${statusLabel(status)} ${path || query || toolCall.toolName}`;
  }
  const labels: Record<string, string> = {
    list_files: running ? "正在列出文件" : "已列出文件",
    read_file: running ? `正在读取 ${path || "文件"}` : `已读取 ${path || "文件"}`,
    read_file_range: running ? `正在读取 ${path || "文件"}` : `已读取 ${path || "文件"}`,
    search_text: running ? `正在搜索 ${query || "文本"}` : `已搜索 ${query || "文本"}`,
    write_file: running ? `正在编辑 ${path || "文件"}` : `已编辑 ${path || "文件"}`,
    apply_patch: running ? `正在编辑 ${path || "文件"}` : `已编辑 ${path || "文件"}`,
    delete_file: running ? `正在删除 ${path || "文件"}` : `已删除 ${path || "文件"}`,
    declare_outputs: running ? "正在声明产物" : result.outcome === "success" ? "已声明产物" : "产物声明未完成",
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

function SkillIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 1.5C8 5 11 8 14.5 8C11 8 8 11 8 14.5C8 11 5 8 1.5 8C5 8 8 5 8 1.5Z" />
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

function DefaultToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2L14 8L8 14L2 8Z" />
    </svg>
  );
}

function toolIcon(name: string): ReactNode {
  if (name === "list_files") return <FileListIcon />;
  if (["read_file", "read_file_range"].includes(name)) return <FileReadIcon />;
  if (["search_text", "tool_search"].includes(name)) return <SearchIcon />;
  if (["write_file", "apply_patch"].includes(name)) return <FileWriteIcon />;
  if (name === "delete_file") return <FileDeleteIcon />;
  if (name.startsWith("skill_")) return <SkillIcon />;
  if (name.startsWith("mcp") || name.includes("__")) return <McpIcon />;
  return <DefaultToolIcon />;
}

function statusLabel(status: Item["status"]): string {
  return ({ in_progress: "运行中", completed: "成功", failed: "失败", declined: "已拒绝", canceled: "已取消" } as const)[status];
}

function safeToolSummary(value: string | undefined, status: Item["status"]): string {
  const parsed = parseObject(value);
  if (isReconciliationGate(parsed)) return "未执行，等待只读核验";
  return stringField(parsed, "summary") || stringField(parsed, "code") || statusLabel(status);
}

function isReconciliationGate(result: Record<string, unknown>): boolean {
  const code = stringField(result, "code");
  return code === "TOOL_RECONCILIATION_REQUIRED"
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

function optionalBooleanField(
  source: Record<string, unknown>,
  key: string,
): boolean | undefined {
  return typeof source[key] === "boolean" ? source[key] : undefined;
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
