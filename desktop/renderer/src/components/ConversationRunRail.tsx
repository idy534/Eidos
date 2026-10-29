import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, RefObject } from "react";
import type { ContextCompactionActivity } from "../../../shared/context.generated.js";
import type { Item, Run } from "../contracts.js";
import { formatInlineReferencesAsPlainText } from "../../../shared/inline-references.js";
import { isQueuedMessage } from "../session-state.js";
import { isFeedAtBottom } from "./useFeedScroll.js";

export interface RunAnchor {
  runId: string;
  userItemId: string;
  userPreview: string;
  assistantPreview: string;
  isStatusPlaceholder: boolean;
  isMounted: boolean;
}

export interface ConversationRunRailProps {
  runs: readonly Run[];
  items: readonly Item[];
  resultItems?: readonly Item[] | undefined;
  supersededRunIds?: ReadonlySet<string> | undefined;
  contextCompactions?: readonly ContextCompactionActivity[] | undefined;
  feedRef: RefObject<HTMLElement | null>;
  pauseFollowing: () => void;
  onEnsureItemMounted?: ((itemId: string) => Promise<boolean>) | undefined;
}

/**
 * 净化助手回答 Markdown 文本，保留可读前几行并去除样式字符。
 * 预先截取前 280 字符，流式后半程不会重复触发正则重算。
 */
export function cleanAssistantPreviewText(raw: string): string {
  const bounded = raw.slice(0, 280);
  return bounded
    .replace(/```[^\n]*\n?/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_~]{1,3}/g, "")
    .replace(/^>\s*/gm, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * 提取主提问预览：还原行内引用、合并空白、截取单行摘要。
 */
export function extractUserPreview(item: Item | undefined): string {
  if (!item || !item.content) return "查看提问";
  const plain = formatInlineReferencesAsPlainText(item.content)
    .replace(/\s+/g, " ")
    .trim();
  return plain || "查看提问";
}

/**
 * 提取助手最终回答的前几行，或在无文本时提供状态回退。
 */
export function extractAssistantPreview(
  run: Run,
  runItems: readonly Item[],
  _compactions?: readonly ContextCompactionActivity[],
): { preview: string; isStatusPlaceholder: boolean } {
  const sorted = [...runItems].sort((a, b) => a.ordinal - b.ordinal);

  // 寻找最后一个非工具调用的最终回答（跳过中间思考与工具调用）
  let lastToolOrdinal = -1;
  for (const it of sorted) {
    if (it.kind !== "assistant_message" && it.toolCall?.toolName !== "write_plan" && it.kind !== "user_message") {
      if (it.ordinal > lastToolOrdinal) lastToolOrdinal = it.ordinal;
    }
  }

  for (let i = sorted.length - 1; i >= 0; i--) {
    const it = sorted[i]!;
    if (it.kind === "assistant_message" && it.ordinal > lastToolOrdinal && it.content && it.content.trim().length > 0) {
      return {
        preview: cleanAssistantPreviewText(it.content),
        isStatusPlaceholder: false,
      };
    }
  }

  // 若无最终回答文本，检查是否有写计划
  if (sorted.some((it) => it.toolCall?.toolName === "write_plan")) {
    return { preview: "已生成执行计划", isStatusPlaceholder: false };
  }

  // 状态回退占位文案
  switch (run.status) {
    case "queued":
      return { preview: "排队中…", isStatusPlaceholder: true };
    case "running": {
      const hasToolActive = sorted.some((it) => it.status === "in_progress" && it.kind !== "assistant_message");
      return { preview: hasToolActive ? "正在执行…" : "正在思考…", isStatusPlaceholder: true };
    }
    case "waiting_input":
      return { preview: "等待回答…", isStatusPlaceholder: true };
    case "waiting_approval":
      return { preview: "等待批准…", isStatusPlaceholder: true };
    case "waiting_agents":
      return { preview: "等待子任务…", isStatusPlaceholder: true };
    case "finalizing":
      return { preview: "正在整理结果…", isStatusPlaceholder: true };
    case "stopped":
      return { preview: "任务已停止", isStatusPlaceholder: true };
    case "interrupted":
      return { preview: "任务执行已中断", isStatusPlaceholder: true };
    case "canceled":
      return { preview: "任务已取消", isStatusPlaceholder: true };
    case "failed":
      return { preview: "任务执行失败", isStatusPlaceholder: true };
    case "succeeded":
    default:
      return { preview: "已完成", isStatusPlaceholder: true };
  }
}

/**
 * 构建全会话稳定的 Run 轮次锚点列表。
 */
export function buildRunAnchors(options: {
  runs: readonly Run[];
  items: readonly Item[];
  resultItems?: readonly Item[] | undefined;
  supersededRunIds?: ReadonlySet<string> | undefined;
  contextCompactions?: readonly ContextCompactionActivity[] | undefined;
}): RunAnchor[] {
  const {
    runs,
    items,
    resultItems = items,
    supersededRunIds,
    contextCompactions,
  } = options;

  const mountedItemIds = new Set(items.map((it) => it.id));
  const itemsByRunId = new Map<string, Item[]>();
  for (const it of resultItems) {
    const list = itemsByRunId.get(it.runId);
    if (list) list.push(it);
    else itemsByRunId.set(it.runId, [it]);
  }

  const validRuns = runs.filter((run) => {
    if (isQueuedMessage(run)) return false;
    if (supersededRunIds && supersededRunIds.has(run.id)) return false;
    return true;
  });

  const anchors: RunAnchor[] = [];

  for (const run of validRuns) {
    const runItems = itemsByRunId.get(run.id) ?? [];
    const userItem = runItems.find((it) => it.kind === "user_message" && it.ordinal <= 1);
    const userItemId = userItem?.id ?? run.id;
    const userPreview = extractUserPreview(userItem);
    const { preview: assistantPreview, isStatusPlaceholder } = extractAssistantPreview(
      run,
      runItems,
      contextCompactions,
    );
    const isMounted = mountedItemIds.has(userItemId);

    anchors.push({
      runId: run.id,
      userItemId,
      userPreview,
      assistantPreview,
      isStatusPlaceholder,
      isMounted,
    });
  }

  return anchors;
}

/**
 * 计算透镜放大宽度：
 * 默认 6px；激活项 26px；前后各 3 项按 [1, 0.7, 0.4, 0.2] 比例放大到 26 / 20 / 14 / 10 / 6px。
 */
export function computeMarkerWidths(count: number, hoverIndex: number): number[] {
  const widths: number[] = new Array(count);
  for (let i = 0; i < count; i++) {
    if (hoverIndex < 0) {
      widths[i] = 6;
    } else {
      const distance = Math.abs(i - hoverIndex);
      const scale = [1, 0.7, 0.4, 0.2][distance] ?? 0;
      widths[i] = 6 + Math.round(scale * 20);
    }
  }
  return widths;
}

/**
 * 自适应行高计算：默认 11px，当轮次很多时平滑压缩至最低 6px。
 */
export function computeItemHeight(availableHeight: number, count: number): number {
  if (count <= 0) return 11;
  const target = Math.floor(availableHeight / count);
  return Math.min(11, Math.max(6, target));
}

const EMPTY_COMPACTIONS: readonly ContextCompactionActivity[] = [];

export const ConversationRunRail = memo(function ConversationRunRail({
  runs,
  items,
  resultItems,
  supersededRunIds,
  contextCompactions = EMPTY_COMPACTIONS,
  feedRef,
  pauseFollowing,
  onEnsureItemMounted,
}: ConversationRunRailProps) {
  const railRef = useRef<HTMLElement | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [hoverIndex, setHoverIndex] = useState<number>(-1);
  const [focusedIndex, setFocusedIndex] = useState<number>(-1);
  const [containerHeight, setContainerHeight] = useState<number>(600);

  const lockedRunIdRef = useRef<string | null>(null);
  const isPointerDownRef = useRef(false);
  const isDraggingRef = useRef(false);
  const pointerStartYRef = useRef(0);

  const anchors = useMemo(() => {
    return buildRunAnchors({
      runs,
      items,
      resultItems,
      supersededRunIds,
      contextCompactions,
    });
  }, [runs, items, resultItems, supersededRunIds, contextCompactions]);

  // 容器高度感知以自适应行高
  useEffect(() => {
    if (anchors.length < 4) return;
    const parent = railRef.current?.parentElement;
    if (!parent) return;

    if (typeof ResizeObserver === "undefined") {
      setContainerHeight(parent.clientHeight || 600);
      return;
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setContainerHeight(entry.contentRect.height);
      }
    });

    observer.observe(parent);
    setContainerHeight(parent.clientHeight || 600);

    return () => observer.disconnect();
  }, [anchors.length]);

  const itemHeight = useMemo(() => {
    const avail = Math.max(200, containerHeight - 64);
    return computeItemHeight(avail, anchors.length);
  }, [containerHeight, anchors.length]);

  // 根据滚动位置实时更新 activeId
  useEffect(() => {
    if (anchors.length < 4) return;
    const feed = feedRef.current;
    if (!feed) return;

    let frame = 0;

    const updateActive = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (lockedRunIdRef.current) return;

        // 若滚动在最底部，优先匹配最后一条已挂载轮次
        if (isFeedAtBottom(feed)) {
          const lastMounted = [...anchors].reverse().find((a) => a.isMounted);
          if (lastMounted) {
            setActiveId(lastMounted.runId);
            return;
          }
        }

        const feedRect = feed.getBoundingClientRect();
        const top = feedRect.top + Math.min(88, feed.clientHeight * 0.25);
        let current = anchors[0]?.runId ?? null;

        for (const anchor of anchors) {
          const el = document.getElementById(`item-${anchor.userItemId}`);
          if (!el) continue;
          if (el.getBoundingClientRect().top <= top) {
            current = anchor.runId;
          } else {
            break;
          }
        }

        setActiveId(current);
      });
    };

    feed.addEventListener("scroll", updateActive, { passive: true });
    updateActive();

    const resizeObserver = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => {
      updateActive();
    }) : undefined;

    if (resizeObserver) {
      resizeObserver.observe(feed);
      if (feed.firstElementChild) {
        resizeObserver.observe(feed.firstElementChild);
      }
    }

    return () => {
      cancelAnimationFrame(frame);
      feed.removeEventListener("scroll", updateActive);
      resizeObserver?.disconnect();
    };
  }, [anchors, feedRef]);

  // 平滑跳转到对应轮次
  const jumpToAnchor = useCallback((anchor: RunAnchor, behavior: ScrollBehavior = "smooth") => {
    pauseFollowing();
    lockedRunIdRef.current = anchor.runId;
    setActiveId(anchor.runId);

    const doScroll = () => {
      const el = document.getElementById(`item-${anchor.userItemId}`);
      const feed = feedRef.current;
      if (!el || !feed) return;

      const feedRect = feed.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      const hasFindBar = Boolean(document.querySelector(".find-bar"));
      const offset = hasFindBar ? 56 : 20;
      const targetScrollTop = feed.scrollTop + (elRect.top - feedRect.top) - offset;

      feed.scrollTo({
        top: Math.max(0, targetScrollTop),
        behavior,
      });

      const unlock = () => {
        if (lockedRunIdRef.current === anchor.runId) {
          lockedRunIdRef.current = null;
        }
      };

      feed.addEventListener("scrollend", unlock, { once: true });
      setTimeout(unlock, 450);
    };

    if (!anchor.isMounted && onEnsureItemMounted) {
      void onEnsureItemMounted(anchor.userItemId).then(() => {
        requestAnimationFrame(() => {
          doScroll();
        });
      });
    } else {
      doScroll();
    }
  }, [feedRef, onEnsureItemMounted, pauseFollowing]);

  // 指针捕获与连续拖拽定位
  const handlePointerDown = (e: PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Ignore unsupported pointer capture
    }
    isPointerDownRef.current = true;
    isDraggingRef.current = false;
    pointerStartYRef.current = e.clientY;
  };

  const handlePointerMove = (e: PointerEvent<HTMLElement>) => {
    const rail = railRef.current;
    if (!rail) return;

    const rect = rail.getBoundingClientRect();
    const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height - 1));
    const index = Math.min(anchors.length - 1, Math.floor(y / itemHeight));

    setHoverIndex(index);

    if (isPointerDownRef.current) {
      const deltaY = Math.abs(e.clientY - pointerStartYRef.current);
      if (deltaY > 3) {
        isDraggingRef.current = true;
        pauseFollowing();

        // 拖拽期间实时定位已挂载轮次
        const anchor = anchors[index];
        if (anchor?.isMounted) {
          jumpToAnchor(anchor, "auto");
        }
      }
    }
  };

  const handlePointerUp = (e: PointerEvent<HTMLElement>) => {
    if (!isPointerDownRef.current) return;
    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
    } catch {
      // Ignore
    }

    const wasDragging = isDraggingRef.current;
    isPointerDownRef.current = false;
    isDraggingRef.current = false;

    const rail = railRef.current;
    if (!rail) return;
    const rect = rail.getBoundingClientRect();
    const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height - 1));
    const index = Math.min(anchors.length - 1, Math.floor(y / itemHeight));
    const anchor = anchors[index];
    if (!anchor) return;

    if (wasDragging) {
      if (!anchor.isMounted) {
        jumpToAnchor(anchor, "auto");
      } else {
        setActiveId(anchor.runId);
      }
    } else {
      jumpToAnchor(anchor, "smooth");
    }
  };

  const handlePointerLeave = () => {
    if (!isPointerDownRef.current && !isDraggingRef.current) {
      setHoverIndex(-1);
    }
  };

  const handlePointerCancel = () => {
    isPointerDownRef.current = false;
    isDraggingRef.current = false;
    setHoverIndex(-1);
  };

  // 键盘无障碍焦点导航
  const handleKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = Math.min(anchors.length - 1, index + 1);
      setHoverIndex(next);
      setFocusedIndex(next);
      const nextBtn = railRef.current?.querySelectorAll<HTMLButtonElement>(".conversation-run-rail-item__btn")[next];
      nextBtn?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const prev = Math.max(0, index - 1);
      setHoverIndex(prev);
      setFocusedIndex(prev);
      const prevBtn = railRef.current?.querySelectorAll<HTMLButtonElement>(".conversation-run-rail-item__btn")[prev];
      prevBtn?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      setHoverIndex(0);
      setFocusedIndex(0);
      const firstBtn = railRef.current?.querySelectorAll<HTMLButtonElement>(".conversation-run-rail-item__btn")[0];
      firstBtn?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      const last = anchors.length - 1;
      setHoverIndex(last);
      setFocusedIndex(last);
      const lastBtn = railRef.current?.querySelectorAll<HTMLButtonElement>(".conversation-run-rail-item__btn")[last];
      lastBtn?.focus();
    } else if (e.key === "Escape") {
      setHoverIndex(-1);
      setFocusedIndex(-1);
    }
  };

  // 少于 4 条用户提问时不显示轨道
  if (anchors.length < 4) return null;

  const markerWidths = computeMarkerWidths(anchors.length, hoverIndex);

  // 计算预览卡片纵向位置，夹紧在父容器内避免上下溢出裁切
  const activeHoverAnchor = hoverIndex >= 0 ? anchors[hoverIndex] : undefined;
  let previewTopInRail = 0;
  if (activeHoverAnchor && railRef.current) {
    const parent = railRef.current.parentElement;
    const parentHeight = parent?.clientHeight || containerHeight || 600;
    const railHeight = anchors.length * itemHeight;
    const railTopInParent = (parentHeight - railHeight) / 2;
    const markerCenterInParent = railTopInParent + (hoverIndex + 0.5) * itemHeight;

    const cardHeight = 110;
    const minCenter = cardHeight / 2 + 16;
    const maxCenter = Math.max(minCenter, parentHeight - cardHeight / 2 - 16);
    const clampedCenter = Math.max(minCenter, Math.min(markerCenterInParent, maxCenter));

    previewTopInRail = clampedCenter - railTopInParent;
  }

  return (
    <nav
      ref={railRef}
      className="conversation-run-rail"
      aria-label="会话导航"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerLeave={handlePointerLeave}
      onPointerCancel={handlePointerCancel}
      style={{ height: `${anchors.length * itemHeight}px` }}
    >
      {anchors.map((anchor, index) => {
        const isCurrentActive = activeId === anchor.runId;
        const width = markerWidths[index] ?? 6;
        const isHovered = hoverIndex === index;
        const isFocused = focusedIndex === index;

        return (
          <div
            key={anchor.runId}
            className="conversation-run-rail-item"
            style={{ height: `${itemHeight}px` }}
          >
            <button
              type="button"
              className="conversation-run-rail-item__btn"
              tabIndex={isCurrentActive || (activeId === null && index === 0) ? 0 : -1}
              aria-label={`跳转到：${anchor.userPreview}`}
              aria-current={isCurrentActive ? "step" : undefined}
              onClick={() => jumpToAnchor(anchor, "smooth")}
              onKeyDown={(e) => handleKeyDown(e, index)}
              onFocus={() => {
                setFocusedIndex(index);
                setHoverIndex(index);
              }}
              onBlur={() => {
                if (focusedIndex === index) {
                  setFocusedIndex(-1);
                  setHoverIndex(-1);
                }
              }}
            >
              <span
                className={`run-rail-marker${isCurrentActive ? " active" : ""}${isHovered ? " is-hovered" : ""}${isFocused ? " is-focused" : ""}`}
                style={{ width: `${width}px` }}
              />
            </button>
          </div>
        );
      })}

      {activeHoverAnchor && (
        <div
          className="conversation-run-rail-preview"
          role="tooltip"
          style={{ top: `${previewTopInRail}px` }}
        >
          <div className="conversation-run-rail-preview__user">
            <strong>{activeHoverAnchor.userPreview}</strong>
          </div>
          <div className="conversation-run-rail-preview__assistant">
            <p className={activeHoverAnchor.isStatusPlaceholder ? "is-placeholder" : undefined}>
              {activeHoverAnchor.assistantPreview}
            </p>
          </div>
        </div>
      )}
    </nav>
  );
});
