import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ContextDetails } from "../../../shared/context-management.js";
import type { ContextUsage } from "../contracts.js";
import { formatTokenCount } from "../context-usage.js";
import { Button } from "./Button.js";

const SECTION_CONFIG: Record<string, { label: string; color: string }> = {
  instructions: { label: "系统提示词", color: "#5260f8" },
  tools: { label: "工具及子智能体", color: "#05b072" },
  conversation: { label: "对话消息", color: "#f59e0b" },
  tool_results: { label: "工具结果", color: "#8b5cf6" },
  summary: { label: "历史摘要", color: "#ec4899" },
  overhead: { label: "协议预留", color: "#06b6d4" },
  other: { label: "其他", color: "#94a3b8" },
};

const SECTION_ORDER: readonly string[] = [
  "instructions",
  "tools",
  "conversation",
  "tool_results",
  "summary",
  "overhead",
  "other",
];

export interface ContextDetailsPanelProps {
  runId: string;
  usage: ContextUsage | undefined;
  triggerRef?: React.RefObject<HTMLButtonElement | null>;
  onClose(): void;
}

export function ContextDetailsPanel({
  runId,
  usage,
  triggerRef,
  onClose,
}: ContextDetailsPanelProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [details, setDetails] = useState<ContextDetails>();
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    dialog.current?.focus();
    return () => {
      alive.current = false;
    };
  }, []);

  useLayoutEffect(() => {
    if (!dialog.current) return;
    const rect = dialog.current.getBoundingClientRect();
    if (rect.left < 8) {
      const shift = 8 - rect.left;
      dialog.current.style.transform = `translateX(${shift}px)`;
    } else if (rect.right > window.innerWidth - 8) {
      const shift = rect.right - (window.innerWidth - 8);
      dialog.current.style.transform = `translateX(-${shift}px)`;
    }
  }, [details]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        event.preventDefault();
        onClose();
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        dialog.current &&
        !dialog.current.contains(target) &&
        !triggerRef?.current?.contains(target)
      ) {
        onClose();
      }
    };
    document.addEventListener("keydown", handleKeyDown, true);
    document.addEventListener("pointerdown", handlePointerDown, true);
    return () => {
      document.removeEventListener("keydown", handleKeyDown, true);
      document.removeEventListener("pointerdown", handlePointerDown, true);
    };
  }, [onClose, triggerRef]);

  useEffect(() => {
    let current = true;
    setLoading(true);
    void window.eidosRuntime
      .readContextDetails(runId)
      .then((value) => {
        if (current) {
          setDetails(value);
          setError("");
        }
      })
      .catch(() => {
        if (current) setError("系统暂时无法读取上下文统计，请关闭后重试。");
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [runId, usage]);

  async function compact() {
    setRequesting(true);
    setError("");
    try {
      const record = await window.eidosRuntime.requestContextCompaction(runId);
      if (alive.current) {
        setDetails((previous) =>
          previous ? { ...previous, manualPending: true, compaction: record } : previous,
        );
      }
    } catch {
      if (alive.current) setError("系统未能安排压缩，请稍后重试。");
    } finally {
      if (alive.current) setRequesting(false);
    }
  }

  const record = details?.compaction;

  const percentUsed = details
    ? details.activeTokens != null && details.contextWindowTokens > 0
      ? (details.activeTokens / details.contextWindowTokens) * 100
      : usage?.percentUsed ?? 0
    : usage?.percentUsed ?? 0;
  const percentDisplay = percentUsed.toFixed(1);
  const activeTokensDisplay =
    details?.activeTokens != null ? formatTokenCount(details.activeTokens) : "--";
  const windowTokensDisplay = details
    ? formatTokenCount(details.contextWindowTokens)
    : "--";

  const sortedSections = [...(details?.sections || [])].sort((a, b) => {
    const indexA = SECTION_ORDER.indexOf(a.name);
    const indexB = SECTION_ORDER.indexOf(b.name);
    return (indexA === -1 ? 99 : indexA) - (indexB === -1 ? 99 : indexB);
  });

  return (
    <dialog
      ref={dialog}
      open
      tabIndex={-1}
      className="context-details-dialog"
      aria-label="上下文详情"
      onClose={onClose}
    >
      <div className="context-details-content">
        <div className="context-details-heading">
          <span className="context-details-title">上下文用量</span>
          <button
            type="button"
            className="context-details-close-btn"
            aria-label="关闭上下文详情"
            onClick={onClose}
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 12 12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            >
              <path d="M1.5 1.5l9 9M10.5 1.5l-9 9" />
            </svg>
          </button>
        </div>

        {loading && !details && <p role="status">系统正在读取统计…</p>}
        {error && <p role="alert">{error}</p>}

        {details && (
          <>
            <div className="context-hero">
              <div className="context-hero-row">
                <span className="context-hero-percent">{percentDisplay}%</span>
                <span className="context-hero-used">
                  已使用 {activeTokensDisplay}/ {windowTokensDisplay}
                </span>
              </div>
            </div>

            <div
              className="context-progress-bar"
              role="progressbar"
              aria-label="上下文用量构成"
              aria-valuenow={Math.round(percentUsed)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              {sortedSections.map((section) => {
                const config =
                  SECTION_CONFIG[section.name] ?? { label: section.name, color: "#94a3b8" };
                const sectionShare =
                  details.contextWindowTokens > 0
                    ? (section.tokens / details.contextWindowTokens) * 100
                    : 0;
                if (sectionShare <= 0 && section.tokens === 0) return null;
                return (
                  <div
                    key={section.name}
                    className="context-progress-segment"
                    style={{
                      width: `${Math.max(sectionShare, 0.4)}%`,
                      backgroundColor: config.color,
                    }}
                    title={`${config.label}: ${formatTokenCount(section.tokens)} (${sectionShare.toFixed(1)}%)`}
                  />
                );
              })}
              {(!details.sections || details.sections.length === 0) && percentUsed > 0 && (
                <div
                  className="context-progress-segment"
                  style={{ width: `${percentUsed}%`, backgroundColor: "#5260f8" }}
                />
              )}
            </div>

            {details.sections && details.sections.length > 0 && (
              <div className="context-sections-list">
                {sortedSections.map((section) => {
                  const config =
                    SECTION_CONFIG[section.name] ?? { label: section.name, color: "#94a3b8" };
                  const sectionShare =
                    details.contextWindowTokens > 0
                      ? (section.tokens / details.contextWindowTokens) * 100
                      : 0;
                  const percentText =
                    section.tokens === 0
                      ? "0%"
                      : sectionShare < 0.1
                        ? "<0.1%"
                        : `${sectionShare.toFixed(1)}%`;
                  return (
                    <div
                      key={section.name}
                      className="context-section-item"
                      title={`${formatTokenCount(section.tokens)} Tokens`}
                    >
                      <div className="context-section-left">
                        <span
                          className="context-section-dot"
                          style={{ backgroundColor: config.color }}
                          aria-hidden="true"
                        />
                        <span className="context-section-name">{config.label}</span>
                      </div>
                      <span className="context-section-value">{percentText}</span>
                    </div>
                  );
                })}
              </div>
            )}

            <Button
              className="context-details-compact"
              variant="primary"
              size="medium"
              disabled={loading || requesting || details.manualPending || record?.status === "running"}
              onClick={() => void compact()}
            >
              {requesting ? "系统正在安排…" : details.manualPending ? "准备开始压缩" : "压缩上下文"}
            </Button>
          </>
        )}
      </div>
    </dialog>
  );
}
