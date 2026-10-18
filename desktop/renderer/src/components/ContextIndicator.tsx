import type { ContextUsage } from "../contracts.js";
import { formatContextUsage } from "../context-usage.js";
import { useRef, useState } from "react";
import { ContextDetailsPanel } from "./ContextDetailsPanel.js";

export interface ContextIndicatorProps {
  usage: ContextUsage | undefined;
  className?: string;
  runId?: string | undefined;
}

export function ContextIndicator({ usage, className, runId }: ContextIndicatorProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const percent = usage ? Math.min(100, Math.max(0, usage.percentUsed)) : 0;
  const size = 18;
  const strokeWidth = 2.5;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (percent / 100) * circumference;
  const formattedUsage = formatContextUsage(usage);

  let arcColor = "var(--muted, #6a6f67)";
  if (percent >= 90) {
    arcColor = "var(--status-danger, #9e352b)";
  } else if (percent >= 80) {
    arcColor = "var(--status-warning, #8a6723)";
  }

  const handleClose = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  return (
    <div
      className={`composer-context-usage${className ? ` ${className}` : ""}`}
      tabIndex={runId ? undefined : 0}
      role="region"
      aria-label={formattedUsage}
      aria-live="polite"
      title="当前模型当前上下文使用量"
    >
      <button
        ref={triggerRef}
        className="context-indicator-trigger"
        type="button"
        disabled={!runId}
        aria-label="查看上下文详情"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <svg
          className="context-progress-ring"
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          aria-hidden="true"
        >
          <circle
            className="context-progress-track"
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={strokeWidth}
          />
          <circle
            className="context-progress-arc"
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={arcColor}
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        </svg>
      </button>
      {!open && (
        <div className="composer-context-tooltip" role="tooltip">
          {formattedUsage}
        </div>
      )}
      {open && runId && (
        <ContextDetailsPanel
          key={runId}
          runId={runId}
          usage={usage}
          triggerRef={triggerRef}
          onClose={handleClose}
        />
      )}
    </div>
  );
}
