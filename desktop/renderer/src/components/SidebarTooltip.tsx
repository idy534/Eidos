import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

import type { Session } from "../contracts.js";

export interface SessionTooltipData {
  kind: "session";
  session: Session;
  projectDisplayName?: string | undefined;
  projectless?: boolean | undefined;
  statusLabel?: string | undefined;
  statusTone?: "success" | "progress" | "error" | undefined;
  rect: DOMRect;
}

export interface ProjectTooltipData {
  kind: "project";
  projectName: string;
  workspaceRoot: string;
  projectless?: boolean | undefined;
  rect: DOMRect;
}

export type SidebarTooltipState = SessionTooltipData | ProjectTooltipData;

export type SidebarTooltipTarget =
  | Omit<SessionTooltipData, "rect">
  | Omit<ProjectTooltipData, "rect">;

/**
 * Format a timestamp into an intuitive relative string and a precise exact date-time string.
 */
export function formatSessionTime(timestamp: number): { friendly: string; exact: string } {
  if (!timestamp || isNaN(timestamp) || timestamp <= 0) {
    return { friendly: "刚刚", exact: "" };
  }

  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);

  const pad = (n: number) => n.toString().padStart(2, "0");
  const year = date.getFullYear();
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());

  const exact = `${year}-${month}-${day} ${hours}:${minutes}`;

  // Within 1 minute
  if (diffMs < 60 * 1000 && diffMs >= -30 * 1000) {
    return { friendly: "刚刚", exact };
  }

  // Within 1 hour
  if (diffMin < 60 && diffMin > 0) {
    return { friendly: `${diffMin} 分钟前`, exact };
  }

  // Same calendar day
  const isToday =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (isToday) {
    return { friendly: `今天 ${hours}:${minutes}`, exact };
  }

  // Yesterday
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const isYesterday =
    date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate();
  if (isYesterday) {
    return { friendly: `昨天 ${hours}:${minutes}`, exact };
  }

  // Same year
  if (date.getFullYear() === now.getFullYear()) {
    return { friendly: `${date.getMonth() + 1}月${date.getDate()}日 ${hours}:${minutes}`, exact };
  }

  // Older years
  return { friendly: `${year}年${date.getMonth() + 1}月${date.getDate()}日`, exact };
}

function FolderIconSvg() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" fillOpacity="0.85" aria-hidden="true">
      <path d="M1.75 2.5A1.75 1.75 0 0 0 0 4.25v7.5C0 12.72.78 13.5 1.75 13.5h12.5A1.75 1.75 0 0 0 16 11.75v-6A1.75 1.75 0 0 0 14.25 4H7.88L6.44 2.56A1.75 1.75 0 0 0 5.2 2.05H1.75Z" />
    </svg>
  );
}

function SessionIconSvg() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.5 3.5h11a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-6.5l-3 2.5v-2.5h-1.5a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1z" />
    </svg>
  );
}

function BranchIconSvg() {
  return (
    <svg viewBox="0 0 14 14" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="3.5" cy="3.5" r="1.5" />
      <circle cx="3.5" cy="10.5" r="1.5" />
      <circle cx="10.5" cy="5.5" r="1.5" />
      <path d="M3.5 5v4M10.5 7v-.5a3 3 0 0 0-3-3h-4" />
    </svg>
  );
}

export interface SidebarTooltipProps {
  state: SidebarTooltipState | null;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

export function SidebarTooltip({ state, onMouseEnter, onMouseLeave }: SidebarTooltipProps) {
  const cardRef = useRef<HTMLDivElement | null>(null);
  const [coords, setCoords] = useState<{
    left: number;
    top: number;
    arrowY: number;
    placement: "right" | "left";
  } | null>(null);

  useLayoutEffect(() => {
    if (!state || !cardRef.current) {
      setCoords(null);
      return;
    }

    const card = cardRef.current;
    const cardRect = card.getBoundingClientRect();
    const targetRect = state.rect;
    const cardHeight = cardRect.height || 120;
    const cardWidth = cardRect.width || 260;

    const MARGIN = 10;
    const padding = 12;

    // Place horizontally to the right of trigger by default
    let left = targetRect.right + MARGIN;
    let placement: "right" | "left" = "right";
    if (left + cardWidth > window.innerWidth - padding) {
      left = Math.max(padding, targetRect.left - cardWidth - MARGIN);
      placement = "left";
    }

    // Align vertically with center of trigger
    const triggerCenterY = targetRect.top + targetRect.height / 2;
    let top = triggerCenterY - cardHeight / 2;

    // Viewport clamping
    if (top < padding) {
      top = padding;
    } else if (top + cardHeight > window.innerHeight - padding) {
      top = window.innerHeight - padding - cardHeight;
    }

    const arrowY = Math.max(14, Math.min(cardHeight - 14, triggerCenterY - top));

    setCoords({ left, top, arrowY, placement });
  }, [state]);

  if (typeof document === "undefined" || !state) {
    return null;
  }

  const tooltipStyle: CSSProperties = coords
    ? ({
        "--tooltip-x": `${Math.round(coords.left)}px`,
        "--tooltip-y": `${Math.round(coords.top)}px`,
        "--arrow-y": `${Math.round(coords.arrowY)}px`,
      } as CSSProperties)
    : { visibility: "hidden", position: "fixed", top: -9999, left: -9999 };

  const content = (
    <div
      ref={cardRef}
      className={`sidebar-hover-card sidebar-hover-card--${state.kind}`}
      role="tooltip"
      id="sidebar-hover-tooltip"
      data-placement={coords?.placement ?? "right"}
      style={tooltipStyle}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="sidebar-hover-card__arrow" aria-hidden="true" />

      {state.kind === "project" ? (
        <>
          <div className="sidebar-hover-card__header">
            <span className="sidebar-hover-card__icon sidebar-hover-card__icon--project" aria-hidden="true">
              <FolderIconSvg />
            </span>
            <span className="sidebar-hover-card__title" title={state.projectName}>
              {state.projectName}
            </span>
            {state.projectless && (
              <span className="sidebar-hover-card__badge">最近会话</span>
            )}
          </div>
          <div className="sidebar-hover-card__body">
            <div className="sidebar-hover-card__section">
              <span className="sidebar-hover-card__label">
                {state.projectless ? "工作区说明" : "文件夹路径"}
              </span>
              <div className="sidebar-hover-card__path-box" title={state.workspaceRoot}>
                {state.projectless && !state.workspaceRoot ? (
                  <span className="sidebar-hover-card__path-empty">未关联独立项目目录（临时工作区）</span>
                ) : (
                  <span className="sidebar-hover-card__path-text">{state.workspaceRoot}</span>
                )}
              </div>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className="sidebar-hover-card__header">
            <span className="sidebar-hover-card__icon sidebar-hover-card__icon--session" aria-hidden="true">
              <SessionIconSvg />
            </span>
            <span className="sidebar-hover-card__title" title={state.session.title ?? "新任务"}>
              {state.session.title?.trim() || "新任务"}
            </span>
          </div>
          <div className="sidebar-hover-card__body">
            {!state.projectless && state.session.projectless !== true && state.projectDisplayName && (
              <div className="sidebar-hover-card__row">
                <span className="sidebar-hover-card__label">所属项目</span>
                <span className="sidebar-hover-card__value sidebar-hover-card__value--project">
                  {state.projectDisplayName}
                </span>
              </div>
            )}

            <div className="sidebar-hover-card__row">
              <span className="sidebar-hover-card__label">最近会话</span>
              <div className="sidebar-hover-card__time-wrapper">
                {(() => {
                  const time = formatSessionTime(state.session.updatedAt || state.session.createdAt);
                  return (
                    <>
                      {time.exact ? (
                        <>
                          <span className="sidebar-hover-card__time-exact">({time.exact})</span>
                          <span className="sidebar-hover-card__time-friendly">{time.friendly}</span>
                        </>
                      ) : (
                        <span className="sidebar-hover-card__time-friendly">
                          {time.friendly}
                        </span>
                      )}
                    </>
                  );
                })()}
              </div>
            </div>

            {state.session.worktree?.branch && (
              <div className="sidebar-hover-card__row">
                <span className="sidebar-hover-card__label">关联分支</span>
                <span className="sidebar-hover-card__branch-pill">
                  <BranchIconSvg />
                  <span>{state.session.worktree.branch}</span>
                </span>
              </div>
            )}

            {state.statusLabel && (
              <div className="sidebar-hover-card__row">
                <span className="sidebar-hover-card__label">任务状态</span>
                <span className={`sidebar-hover-card__status sidebar-hover-card__status--${state.statusTone ?? "progress"}`}>
                  <span className="sidebar-hover-card__status-dot" />
                  <span>{state.statusLabel}</span>
                </span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );

  return createPortal(content, document.body);
}
