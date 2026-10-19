import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent, TouchEvent, UIEvent, WheelEvent } from "react";

export function isFeedAtBottom(feed: Pick<HTMLElement, "scrollHeight" | "scrollTop" | "clientHeight">): boolean {
  return feed.scrollHeight - feed.scrollTop - feed.clientHeight <= 16;
}

export interface UseFeedScrollOptions {
  onLoadOlder?: (() => void) | undefined;
  loadingOlder?: boolean | undefined;
  firstItemId?: string | undefined;
}

/** Following is user intent; distance from the bottom is only a layout measurement. */
export function useFeedScroll(hasContent: boolean, options?: UseFeedScrollOptions) {
  const feedRef = useRef<HTMLElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const previousTopRef = useRef(0);
  const previousSizeRef = useRef({ height: 0, client: 0 });
  const userScrollRef = useRef(false);
  const touchYRef = useRef<number | undefined>(undefined);
  const frameRef = useRef<number | null>(null);
  const [following, setFollowing] = useState(true);
  const [atBottom, setAtBottom] = useState(true);

  const onLoadOlderRef = useRef(options?.onLoadOlder);
  onLoadOlderRef.current = options?.onLoadOlder;
  const loadingOlderRef = useRef(options?.loadingOlder);
  loadingOlderRef.current = options?.loadingOlder;
  const loadTriggeredRef = useRef(false);
  const previousFirstItemIdRef = useRef<string | undefined>(options?.firstItemId);

  useEffect(() => {
    if (!options?.loadingOlder) {
      loadTriggeredRef.current = false;
    }
  }, [options?.loadingOlder, options?.onLoadOlder]);

  const checkAutoLoad = useCallback((element?: HTMLElement | null) => {
    const feed = element ?? feedRef.current;
    if (!feed) return;
    if (!onLoadOlderRef.current || loadingOlderRef.current || loadTriggeredRef.current) return;
    if (feed.scrollTop <= 150) {
      loadTriggeredRef.current = true;
      onLoadOlderRef.current();
    }
  }, []);

  const changeFollowing = useCallback((value: boolean) => {
    followingRef.current = value;
    setFollowing(value);
  }, []);

  const scheduleFollow = useCallback(() => {
    const feed = feedRef.current;
    if (!feed) return;
    if (!followingRef.current) {
      setAtBottom(isFeedAtBottom(feed));
      return;
    }
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const target = feedRef.current;
      if (!target) return;
      if (followingRef.current) target.scrollTop = target.scrollHeight;
      previousTopRef.current = target.scrollTop;
      previousSizeRef.current = { height: target.scrollHeight, client: target.clientHeight };
      setAtBottom(isFeedAtBottom(target));
    });
  }, []);

  useLayoutEffect(() => {
    const feed = feedRef.current;
    if (!feed) return;
    const currentFirstId = options?.firstItemId;
    const previousFirstId = previousFirstItemIdRef.current;
    previousFirstItemIdRef.current = currentFirstId;

    if (
      !followingRef.current &&
      previousFirstId !== undefined &&
      currentFirstId !== undefined &&
      currentFirstId !== previousFirstId
    ) {
      const oldHeight = previousSizeRef.current.height;
      const newHeight = feed.scrollHeight;
      const heightDiff = newHeight - oldHeight;
      if (heightDiff > 0) {
        if (Math.abs(feed.scrollTop - previousTopRef.current) < 5) {
          feed.scrollTop = previousTopRef.current + heightDiff;
        }
        previousTopRef.current = feed.scrollTop;
        previousSizeRef.current = { height: newHeight, client: feed.clientHeight };
        setAtBottom(isFeedAtBottom(feed));
      }
    }
  }, [options?.firstItemId]);

  useLayoutEffect(() => {
    const feed = feedRef.current;
    const content = contentRef.current;
    if (!feed || !content) return;
    // Observe the content as well as the viewport: details animations and late
    // image/code rendering can resize content without changing React props.
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(scheduleFollow) : undefined;
    observer?.observe(content);
    observer?.observe(feed);
    feed.addEventListener("toggle", scheduleFollow, true);
    return () => {
      observer?.disconnect();
      feed.removeEventListener("toggle", scheduleFollow, true);
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    };
  }, [hasContent, scheduleFollow]);

  const handleScroll = useCallback((event: UIEvent<HTMLElement>) => {
    const feed = event.currentTarget;
    const bottom = isFeedAtBottom(feed);
    const resized = feed.scrollHeight !== previousSizeRef.current.height
      || feed.clientHeight !== previousSizeRef.current.client;
    // Layout growth must not cancel following. A decreasing scrollTop away
    // from the bottom also covers scrollbar drags and assistive navigation.
    const movedDown = feed.scrollTop > previousTopRef.current + 1;
    const movedUp = feed.scrollTop < previousTopRef.current - 1;
    if (bottom && (followingRef.current || userScrollRef.current || (movedDown && !resized))) changeFollowing(true);
    else if (movedUp) changeFollowing(false);
    previousTopRef.current = feed.scrollTop;
    previousSizeRef.current = { height: feed.scrollHeight, client: feed.clientHeight };
    userScrollRef.current = false;
    setAtBottom(bottom);

    if (movedUp || !followingRef.current) {
      checkAutoLoad(feed);
    }
  }, [changeFollowing, checkAutoLoad]);

  const handleWheel = useCallback((event: WheelEvent<HTMLElement>) => {
    userScrollRef.current = true;
    if (event.deltaY < 0) {
      changeFollowing(false);
      checkAutoLoad(event.currentTarget);
    }
  }, [changeFollowing, checkAutoLoad]);

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (event.target instanceof Element && event.target.closest("input, textarea, [contenteditable=true]")) return;
    if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) {
      userScrollRef.current = true;
      changeFollowing(false);
      checkAutoLoad(event.currentTarget);
    } else if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) {
      userScrollRef.current = true;
    }
  }, [changeFollowing, checkAutoLoad]);

  const handlePointerDown = useCallback((event: PointerEvent<HTMLElement>) => {
    const feed = event.currentTarget;
    const right = feed.getBoundingClientRect().left + feed.clientLeft + feed.clientWidth;
    if (event.target === feed && event.clientX >= right) {
      userScrollRef.current = true;
      changeFollowing(false);
    }
  }, [changeFollowing]);

  const handleTouchStart = useCallback((event: TouchEvent<HTMLElement>) => {
    touchYRef.current = event.touches[0]?.clientY;
  }, []);

  const handleTouchMove = useCallback((event: TouchEvent<HTMLElement>) => {
    userScrollRef.current = true;
    const y = event.touches[0]?.clientY;
    if (y !== undefined && touchYRef.current !== undefined && y > touchYRef.current) {
      changeFollowing(false);
      checkAutoLoad(event.currentTarget);
    }
    touchYRef.current = y;
  }, [changeFollowing, checkAutoLoad]);

  const jumpToBottom = useCallback(() => {
    changeFollowing(true);
    // Use the same writer as streaming/resize following. A separate smooth
    // scroll would compete with incoming content and use an obsolete target.
    scheduleFollow();
  }, [changeFollowing, scheduleFollow]);

  return {
    feedRef, contentRef, atBottom, following, scheduleFollow, jumpToBottom,
    handleScroll, handleWheel, handleKeyDown, handlePointerDown, handleTouchStart, handleTouchMove,
  };
}
