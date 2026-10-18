import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLayoutEffect } from "react";
import { useFeedScroll } from "./useFeedScroll.js";

function Feed({ empty = false }: { empty?: boolean }) {
  const scroll = useFeedScroll(!empty);
  useLayoutEffect(scroll.scheduleFollow, [empty, scroll.scheduleFollow]);
  if (empty) return <p>Empty</p>;
  return <>
    <section ref={scroll.feedRef} data-testid="feed" data-following={scroll.following}
      onScroll={scroll.handleScroll} onWheel={scroll.handleWheel} onKeyDown={scroll.handleKeyDown}
      onTouchStart={scroll.handleTouchStart} onTouchMove={scroll.handleTouchMove}>
      <div ref={scroll.contentRef}>Content</div>
    </section>
    <button hidden={scroll.atBottom} onClick={scroll.jumpToBottom}>Latest</button>
  </>;
}

describe("feed following intent and layout", () => {
  let frames: Map<number, FrameRequestCallback>;
  let resize: ResizeObserverCallback;
  let observe: ReturnType<typeof vi.fn>;
  let disconnect: ReturnType<typeof vi.fn>;
  let frameId: number;

  beforeEach(() => {
    frames = new Map();
    frameId = 0;
    observe = vi.fn();
    disconnect = vi.fn();
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.set(++frameId, callback);
      return frameId;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => { frames.delete(id); });
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: ResizeObserverCallback) { resize = callback; }
      observe = observe;
      disconnect = disconnect;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  function flush() {
    act(() => {
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback(16));
    });
  }

  function metrics(feed: HTMLElement, height: number, top: number) {
    Object.defineProperty(feed, "scrollHeight", { configurable: true, value: height });
    Object.defineProperty(feed, "clientHeight", { configurable: true, value: 600 });
    // Model browser clamping, which jsdom does not implement.
    Object.defineProperty(feed, "scrollTop", {
      configurable: true, get: () => top,
      set: (value: number) => { top = Math.max(0, Math.min(height - 600, value)); },
    });
  }

  function resized() { act(() => resize([], {} as ResizeObserver)); }

  it("follows every animation/late-resource resize without a React content update", () => {
    render(<Feed />);
    const feed = screen.getByTestId("feed");
    metrics(feed, 1_000, 400);
    flush();
    for (const height of [1_100, 1_500, 2_200]) {
      metrics(feed, height, feed.scrollTop);
      resized();
      flush();
      fireEvent.scroll(feed);
      expect(feed.scrollTop).toBe(height - 600);
      expect(feed).toHaveAttribute("data-following", "true");
      expect(screen.getByText("Latest")).not.toBeVisible();
    }
  });

  it.each(["wheel", "keyboard", "touch"])("lets upward %s input interrupt an already queued follow", (input) => {
    render(<Feed />);
    const feed = screen.getByTestId("feed");
    metrics(feed, 1_000, 400);
    flush();
    metrics(feed, 1_200, 400);
    resized();
    if (input === "wheel") fireEvent.wheel(feed, { deltaY: -100 });
    if (input === "keyboard") fireEvent.keyDown(feed, { key: "PageUp" });
    if (input === "touch") {
      fireEvent.touchStart(feed, { touches: [{ clientY: 100 }] });
      fireEvent.touchMove(feed, { touches: [{ clientY: 150 }] });
    }
    flush();
    expect(feed.scrollTop).toBe(400);
    expect(feed).toHaveAttribute("data-following", "false");
    expect(screen.getByText("Latest")).toBeVisible();
  });

  it("keeps history paused across collapse clamping and expansion", () => {
    render(<Feed />);
    const feed = screen.getByTestId("feed");
    metrics(feed, 2_000, 1_400);
    flush();
    fireEvent.wheel(feed, { deltaY: -100 });
    metrics(feed, 2_000, 800);
    fireEvent.scroll(feed);
    metrics(feed, 600, 0);
    fireEvent.scroll(feed);
    // Chromium may deliver another scroll event after the final animation frame.
    fireEvent.scroll(feed);
    resized();
    metrics(feed, 2_000, 0);
    resized();
    flush();
    expect(feed.scrollTop).toBe(0);
    expect(feed).toHaveAttribute("data-following", "false");
    expect(screen.getByText("Latest")).toBeVisible();
  });

  it("resumes following on Latest and tracks content arriving before the frame", () => {
    render(<Feed />);
    const feed = screen.getByTestId("feed");
    metrics(feed, 2_000, 1_400);
    flush();
    metrics(feed, 2_000, 500);
    fireEvent.scroll(feed);
    fireEvent.click(screen.getByText("Latest"));
    metrics(feed, 2_500, 500);
    resized();
    flush();
    expect(feed.scrollTop).toBe(1_900);
    expect(feed).toHaveAttribute("data-following", "true");
  });

  it("resumes after the user scrolls back to the bottom", () => {
    render(<Feed />);
    const feed = screen.getByTestId("feed");
    metrics(feed, 2_000, 1_400);
    flush();
    metrics(feed, 2_000, 500);
    fireEvent.scroll(feed);
    expect(feed).toHaveAttribute("data-following", "false");
    fireEvent.wheel(feed, { deltaY: 900 });
    metrics(feed, 2_000, 1_400);
    fireEvent.scroll(feed);
    expect(feed).toHaveAttribute("data-following", "true");
    metrics(feed, 2_200, 1_400);
    resized();
    flush();
    expect(feed.scrollTop).toBe(1_600);
  });

  it("attaches after an empty feed gains content and releases pending work", () => {
    const { rerender, unmount } = render(<Feed empty />);
    expect(observe).not.toHaveBeenCalled();
    rerender(<Feed />);
    expect(observe).toHaveBeenCalledTimes(2);
    expect(frames.size).toBe(1);
    unmount();
    expect(disconnect).toHaveBeenCalledOnce();
    expect(frames.size).toBe(0);
  });
});
