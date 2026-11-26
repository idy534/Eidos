import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { Item } from "../contracts.js";

export const VIRTUALIZE_MIN_GROUPS = 24;
export const ESTIMATED_RUN_HEIGHT = 240;
export const OVERSCAN_PX = 800;
export const MIN_OVERSCAN_GROUPS = 4;

const VIEWPORT_SHIFT_THRESHOLD_PX = 32;

export interface RunGroupEntry {
  runId: string;
  items: Item[];
}

export interface FeedVirtualizationResult {
  active: boolean;
  isMounted: (index: number, runId: string) => boolean;
  heightFor: (runId: string) => number;
  measureRef: (runId: string) => (node: HTMLDivElement | null) => void;
}

export function useFeedVirtualization(
  feedRef: RefObject<HTMLElement | null>,
  groups: readonly RunGroupEntry[],
  following: boolean,
  forcedRunIds: ReadonlySet<string>,
): FeedVirtualizationResult {
  const heightsRef = useRef<Map<string, number>>(new Map());
  const nodesRef = useRef<Map<string, HTMLDivElement>>(new Map());
  const observerRef = useRef<ResizeObserver | undefined>(undefined);
  const measureFrameRef = useRef<number | null>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const [viewport, setViewport] = useState<{ scrollTop: number; height: number }>({
    scrollTop: 0,
    height: 0,
  });
  const [measureVersion, setMeasureVersion] = useState(0);

  const scheduleMeasureBump = useCallback(() => {
    if (measureFrameRef.current !== null) return;
    measureFrameRef.current = requestAnimationFrame(() => {
      measureFrameRef.current = null;
      setMeasureVersion((v) => v + 1);
    });
  }, []);

  const canVirtualize = groups.length > VIRTUALIZE_MIN_GROUPS;
  const shouldVirtualize = canVirtualize && viewport.height > 0;
  const activeRef = useRef(shouldVirtualize);
  activeRef.current = shouldVirtualize;

  useEffect(() => {
    const feed = feedRef.current;
    if (!feed || !canVirtualize) return;

    const readViewport = () => ({
      scrollTop: feed.scrollTop,
      height: feed.clientHeight,
    });

    const syncViewport = () => {
      const next = readViewport();
      setViewport((prev) => {
        if (
          prev.height === next.height
          && Math.abs(prev.scrollTop - next.scrollTop) < VIEWPORT_SHIFT_THRESHOLD_PX
        ) {
          return prev;
        }
        return next;
      });
    };

    syncViewport();

    const handleScroll = () => {
      if (scrollFrameRef.current !== null) return;
      scrollFrameRef.current = requestAnimationFrame(() => {
        scrollFrameRef.current = null;
        syncViewport();
      });
    };

    feed.addEventListener("scroll", handleScroll, { passive: true });
    const feedObserver = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(syncViewport)
      : undefined;
    feedObserver?.observe(feed);

    return () => {
      feed.removeEventListener("scroll", handleScroll);
      feedObserver?.disconnect();
      if (scrollFrameRef.current !== null) {
        cancelAnimationFrame(scrollFrameRef.current);
        scrollFrameRef.current = null;
      }
    };
  }, [feedRef, canVirtualize]);

  useEffect(() => {
    if (!canVirtualize || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const element = entry.target as HTMLElement;
        const runId = element.dataset.runGroup;
        if (!runId) continue;
        const measured = Math.round(
          (entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height)
          || element.getBoundingClientRect().height,
        );
        if (measured <= 0) continue;
        const previous = heightsRef.current.get(runId);
        if (previous === undefined || Math.abs(previous - measured) > 2) {
          heightsRef.current.set(runId, measured);
          changed = true;
        }
      }
      if (changed && activeRef.current) {
        scheduleMeasureBump();
      }
    });
    observerRef.current = observer;
    for (const node of nodesRef.current.values()) {
      observer.observe(node);
    }
    return () => {
      observer.disconnect();
      observerRef.current = undefined;
      if (measureFrameRef.current !== null) {
        cancelAnimationFrame(measureFrameRef.current);
        measureFrameRef.current = null;
      }
    };
  }, [canVirtualize, scheduleMeasureBump]);

  const refCallbacksRef = useRef<Map<string, (node: HTMLDivElement | null) => void>>(new Map());
  const measureRef = useCallback((runId: string) => {
    let callback = refCallbacksRef.current.get(runId);
    if (!callback) {
      callback = (node: HTMLDivElement | null) => {
        const previous = nodesRef.current.get(runId);
        if (previous && previous !== node) {
          observerRef.current?.unobserve(previous);
          nodesRef.current.delete(runId);
        }
        if (node) {
          nodesRef.current.set(runId, node);
          observerRef.current?.observe(node);
          const height = Math.round(node.getBoundingClientRect().height || node.offsetHeight || 0);
          if (height > 0 && heightsRef.current.get(runId) === undefined) {
            heightsRef.current.set(runId, height);
          }
        }
      };
      refCallbacksRef.current.set(runId, callback);
    }
    return callback;
  }, []);

  const range = useMemo(() => {
    if (!shouldVirtualize) {
      return { start: 0, end: groups.length - 1 };
    }
    const windowStart = Math.max(0, viewport.scrollTop - OVERSCAN_PX);
    const windowEnd = viewport.scrollTop + viewport.height + OVERSCAN_PX;
    let firstVisible = -1;
    let lastVisible = -1;
    let offset = 0;
    for (let index = 0; index < groups.length; index += 1) {
      const group = groups[index]!;
      const height = heightsRef.current.get(group.runId) ?? ESTIMATED_RUN_HEIGHT;
      const top = offset;
      const bottom = top + height;
      offset = bottom;
      if (bottom >= windowStart && top <= windowEnd) {
        if (firstVisible === -1) firstVisible = index;
        lastVisible = index;
      }
    }
    if (firstVisible === -1) {
      return {
        start: Math.max(0, groups.length - 1 - MIN_OVERSCAN_GROUPS),
        end: groups.length - 1,
      };
    }
    return {
      start: Math.max(0, firstVisible - MIN_OVERSCAN_GROUPS),
      end: Math.min(groups.length - 1, lastVisible + MIN_OVERSCAN_GROUPS),
    };
    // measureVersion forces window recalculation after ResizeObserver updates stored heights.
  }, [shouldVirtualize, groups, viewport.scrollTop, viewport.height, measureVersion]);

  const heightFor = useCallback((runId: string) => {
    return heightsRef.current.get(runId) ?? ESTIMATED_RUN_HEIGHT;
  }, []);

  const isMounted = useCallback((index: number, runId: string) => {
    if (!shouldVirtualize) return true;
    if (index >= range.start && index <= range.end) return true;
    if (following && index >= groups.length - 3) return true;
    return forcedRunIds.has(runId);
  }, [shouldVirtualize, range.start, range.end, following, groups.length, forcedRunIds]);

  return {
    active: shouldVirtualize,
    isMounted,
    heightFor,
    measureRef,
  };
}
