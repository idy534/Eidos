import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { LiveThinking } from "../live-thinking.js";

/** A text change waits at least this long so fast summaries never flicker. */
const MIN_DWELL_MS = 1200;
const POPOVER_GAP_PX = 6;
const BOUNDARY_PADDING_PX = 12;
const FALLBACK_POPOVER_HEIGHT_PX = 160;

type PopoverPlacement = "above" | "below";
type ExpandableThinking = LiveThinking & { body: string };

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

function useSteadyText(next: string): string {
  const [shown, setShown] = useState(next);
  // 0: the first replacement of the initial text applies at once; only later ones are held.
  const changedAt = useRef(0);
  useEffect(() => {
    if (next === shown) return undefined;
    const apply = () => {
      changedAt.current = Date.now();
      setShown(next);
    };
    const wait = changedAt.current + MIN_DWELL_MS - Date.now();
    if (wait <= 0) {
      apply();
      return undefined;
    };
    const timer = window.setTimeout(apply, wait);
    return () => window.clearTimeout(timer);
  }, [next, shown]);
  return shown;
}

const MARK_PATH = "M5 2.5H11A2.5 2.5 0 0 1 13.5 5V10L10 13.5H5A2.5 2.5 0 0 1 2.5 11V5A2.5 2.5 0 0 1 5 2.5Z";

/** The Eidos mark's outline with one bright segment travelling around it. */
function MarkTraceIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="none" strokeWidth="1.5" aria-hidden="true">
      <path className="live-activity-track" pathLength={100} d={MARK_PATH} />
      <path className="live-activity-trace" pathLength={100} d={MARK_PATH} />
    </svg>
  );
}

/**
 * The one moving element at the end of a running Run. It shows the latest
 * provider reasoning summary title, or the current action as a fallback.
 * With summary text it becomes a button that opens a popover above the row
 * (or flips below when near the top of the Feed), so the Feed never shifts.
 */
export function LiveActivityLine({ text, thinking }: { text: string; thinking?: LiveThinking | undefined }) {
  const shown = useSteadyText(text);
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
  const expandable = activeThinking !== undefined;

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
  }, [open, activeThinking?.summaryId, activeThinking?.title, activeThinking?.body, activeThinking?.previous.length]);

  const label = (
    <>
      <span className="live-activity-lead" aria-hidden="true"><MarkTraceIcon /></span>
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
      {open && activeThinking && (
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
      )}
    </div>
  );
}
