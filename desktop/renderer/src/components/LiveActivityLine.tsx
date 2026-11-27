import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { LiveApprovalReview } from "../live-approval-review.js";
import type { LiveThinking } from "../live-thinking.js";

/** A text change waits at least this long so fast summaries never flicker. */
const MIN_DWELL_MS = 1200;
const POPOVER_GAP_PX = 6;
const BOUNDARY_PADDING_PX = 12;
const FALLBACK_POPOVER_HEIGHT_PX = 160;

type PopoverPlacement = "above" | "below";
type ExpandableThinking = LiveThinking & { body: string };

export interface LiveActivityLineProps {
  text: string;
  thinking?: LiveThinking | undefined;
  iconKind?: "mark" | "shield" | undefined;
  approvalReview?: LiveApprovalReview | undefined;
}

function isExpandableThinking(thinking: LiveThinking | undefined): thinking is ExpandableThinking {
  return thinking !== undefined && thinking.body !== undefined && thinking.body !== "";
}

function resolvePopoverPlacement(rootEl: HTMLElement, popoverEl: HTMLElement): PopoverPlacement {
  const feedEl = rootEl.closest(".feed");
  const boundaryRect = feedEl
    ? feedEl.getBoundingClientRect()
    : { top: 0, bottom: window.innerHeight };
  const rowRect = rootEl.getBoundingClientRect();
  const popoverRect = popoverEl.getBoundingClientRect();
  const measuredHeight = popoverEl.offsetHeight || popoverRect.height;
  const popoverHeight = measuredHeight > 0
    ? measuredHeight
    : (rowRect.top !== 0 || rowRect.bottom !== 0 ? FALLBACK_POPOVER_HEIGHT_PX : 0);

  const spaceAbove = rowRect.top - boundaryRect.top - BOUNDARY_PADDING_PX - POPOVER_GAP_PX;
  const spaceBelow = boundaryRect.bottom - rowRect.bottom - BOUNDARY_PADDING_PX - POPOVER_GAP_PX;

  if (popoverHeight > 0 && spaceAbove < popoverHeight && spaceBelow > spaceAbove) {
    return "below";
  }
  return "above";
}

function useSteadyText(next: string, immediate = false): string {
  const [shown, setShown] = useState(next);
  // 0: the first replacement of the initial text applies at once; only later ones are held.
  const changedAt = useRef(0);
  useEffect(() => {
    if (immediate) {
      changedAt.current = Date.now();
      setShown(next);
      return undefined;
    }
    if (next === shown) return undefined;
    const apply = () => {
      changedAt.current = Date.now();
      setShown(next);
    };
    const wait = changedAt.current + MIN_DWELL_MS - Date.now();
    if (wait <= 0) {
      apply();
      return undefined;
    }
    const timer = window.setTimeout(apply, wait);
    return () => window.clearTimeout(timer);
  }, [immediate, next, shown]);
  return immediate ? next : shown;
}

const MARK_PATH = "M5 2.5H11A2.5 2.5 0 0 1 13.5 5V10L10 13.5H5A2.5 2.5 0 0 1 2.5 11V5A2.5 2.5 0 0 1 5 2.5Z";
const SHIELD_PATH = "M8 1.9L13.2 4.1V8.1C13.2 11.4 10.9 13.7 8 14.7C5.1 13.7 2.8 11.4 2.8 8.1V4.1L8 1.9Z";

/** The Eidos mark's outline with one bright segment travelling around it. */
function MarkTraceIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" strokeWidth="1.5" aria-hidden="true">
      <path className="live-activity-track" pathLength={100} d={MARK_PATH} />
      <path className="live-activity-trace" pathLength={100} d={MARK_PATH} />
    </svg>
  );
}

/** Shield outline trace for live auto-review and approval synchronization. */
function ShieldTraceIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" strokeWidth="1.5" strokeLinejoin="round" aria-hidden="true">
      <path className="live-activity-track" pathLength={100} d={SHIELD_PATH} />
      <path className="live-activity-trace" pathLength={100} d={SHIELD_PATH} />
    </svg>
  );
}

/**
 * The one moving element at the end of a running Run. It shows the latest
 * provider reasoning summary title, auto-review stage progress, or current
 * action fallback. With summary text or auto-review details it expands a
 * popover above (or below near the top of the Feed) without shifting layout.
 */
export function LiveActivityLine({
  text,
  thinking,
  iconKind = "mark",
  approvalReview,
}: LiveActivityLineProps) {
  const shown = useSteadyText(text, iconKind === "shield");
  const popoverId = useId();
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<PopoverPlacement>("above");
  const [retainedThinking, setRetainedThinking] = useState<ExpandableThinking | undefined>(
    () => (isExpandableThinking(thinking) ? thinking : undefined),
  );
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  if (isExpandableThinking(thinking) && retainedThinking !== thinking) {
    setRetainedThinking(thinking);
  } else if (!open && !isExpandableThinking(thinking) && retainedThinking !== undefined) {
    setRetainedThinking(undefined);
  }

  const activeThinking = isExpandableThinking(thinking) ? thinking : (open ? retainedThinking : undefined);
  const expandable = approvalReview !== undefined || activeThinking !== undefined;

  useEffect(() => {
    if (!expandable && open) {
      setOpen(false);
    }
  }, [expandable, open]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !rootRef.current || !popoverRef.current) return undefined;
    const updatePlacement = () => {
      if (!rootRef.current || !popoverRef.current) return;
      setPlacement(resolvePopoverPlacement(rootRef.current, popoverRef.current));
    };
    updatePlacement();
    const feedEl = rootRef.current.closest(".feed");
    feedEl?.addEventListener("scroll", updatePlacement, { passive: true });
    window.addEventListener("resize", updatePlacement);
    return () => {
      feedEl?.removeEventListener("scroll", updatePlacement);
      window.removeEventListener("resize", updatePlacement);
    };
  }, [
    open,
    activeThinking?.summaryId,
    activeThinking?.title,
    activeThinking?.body,
    activeThinking?.previous.length,
    approvalReview?.itemId,
    approvalReview?.stage,
    approvalReview?.stepIndex,
    approvalReview?.actionTitle,
    approvalReview?.preview,
  ]);

  const label = (
    <>
      <span className="live-activity-lead" aria-hidden="true">
        {iconKind === "shield" ? <ShieldTraceIcon /> : <MarkTraceIcon />}
      </span>
      <span className="live-activity-text" key={shown} title={shown}>{shown}</span>
    </>
  );

  return (
    <div className="live-activity" role="status" ref={rootRef} data-placement={open ? placement : undefined}>
      {expandable ? (
        <button
          ref={triggerRef}
          type="button"
          className="live-activity-row"
          aria-expanded={open}
          aria-controls={open ? popoverId : undefined}
          aria-haspopup="dialog"
          onClick={() => setOpen((value) => !value)}
        >
          {label}
          <span className="live-activity-more" aria-hidden="true" />
        </button>
      ) : (
        <div className="live-activity-row">{label}</div>
      )}
      {open && approvalReview ? (
        <div
          ref={popoverRef}
          className="live-activity-popover"
          data-placement={placement}
          id={popoverId}
          role="dialog"
          aria-label="自动审批进度"
        >
          <div className="live-activity-popover-header">
            <p className="live-activity-popover-caption">自动审批中</p>
            <span className="live-activity-popover-badge">替我审批</span>
          </div>
          <p className="live-activity-popover-title">{approvalReview.actionTitle}</p>
          {approvalReview.preview && (
            <pre className="live-activity-popover-cmd">{approvalReview.preview}</pre>
          )}
          <ol className="live-activity-review-steps">
            {[
              {
                label: "解析操作与权限范围",
                state: approvalReview.stepIndex > 1 ? "done" : "active",
              },
              {
                label: approvalReview.stage === "retrying"
                  ? "重试安全评估"
                  : "模型评估安全风险与任务匹配度",
                state: approvalReview.stepIndex > 2
                  ? "done"
                  : approvalReview.stepIndex === 2
                    ? "active"
                    : "pending",
              },
              {
                label: "生成审批结论",
                state: approvalReview.stepIndex >= 3 ? "active" : "pending",
              },
            ].map((step, index) => (
              <li key={index} data-state={step.state}>
                <span className="live-activity-review-step-dot" aria-hidden="true">
                  {step.state === "done" ? "✓" : step.state === "active" ? "●" : "○"}
                </span>
                <span>{step.label}</span>
              </li>
            ))}
          </ol>
        </div>
      ) : (open && activeThinking && (
        <div
          ref={popoverRef}
          className="live-activity-popover"
          data-placement={placement}
          id={popoverId}
          role="dialog"
          aria-label="思考过程"
        >
          <p className="live-activity-popover-caption">思考过程</p>
          <p className="live-activity-popover-title">{activeThinking.title}</p>
          <p className="live-activity-popover-body">{activeThinking.body}</p>
          {activeThinking.previous.length > 0 && (
            <ul className="live-activity-popover-previous">
              {activeThinking.previous.map((title, index) => <li key={`${index}:${title}`} title={title}>{title}</li>)}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
