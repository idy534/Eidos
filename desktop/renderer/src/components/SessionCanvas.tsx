import { useLayoutEffect, useRef, type ReactNode } from "react";
import { WelcomeMessage } from "./WelcomeMessage.js";

interface SessionCanvasProps {
  sessionId: string;
  welcome: boolean;
  feed: ReactNode;
  children: ReactNode;
}

/** Keeps the same Composer mounted when the first task replaces the welcome page. */
export function SessionCanvas({ sessionId, welcome, feed, children }: SessionCanvasProps) {
  const composerRef = useRef<HTMLDivElement>(null);
  const previous = useRef<{ sessionId: string; welcome: boolean; top: number } | undefined>(undefined);
  const animation = useRef<Animation | undefined>(undefined);

  useLayoutEffect(() => {
    if (!welcome) return;
    const wrapper = composerRef.current;
    const canvas = wrapper?.closest<HTMLElement>(".session-canvas");
    const surface = wrapper?.querySelector<HTMLElement>(".composer");
    const input = surface?.querySelector("textarea");
    if (!wrapper || !canvas || !surface || !input) return;
    const updateLimit = () => {
      if (!canvas.clientHeight) return;
      // Measure layout rather than the animated surface. The existing Composer
      // owns its chrome and native field sizing; only the available space varies.
      const chromeHeight = surface.offsetHeight - input.offsetHeight;
      const bottomGap = parseFloat(getComputedStyle(canvas).paddingBottom);
      const minimum = parseFloat(getComputedStyle(input).minHeight);
      const limit = Math.max(minimum, canvas.clientHeight - surface.offsetTop - chromeHeight - bottomGap);
      wrapper.style.setProperty("--welcome-input-max-height", `${limit}px`);
    };
    updateLimit();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateLimit) : undefined;
    observer?.observe(canvas);
    observer?.observe(surface);
    window.addEventListener("resize", updateLimit);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateLimit);
      wrapper.style.removeProperty("--welcome-input-max-height");
    };
  }, [welcome, sessionId]);

  useLayoutEffect(() => {
    const composer = composerRef.current;
    if (!composer) return;
    const before = previous.current;
    // The parent wrapper stays untransformed, so a new render measures layout,
    // even while its child is moving toward the bottom of the conversation.
    const top = composer.getBoundingClientRect().top;
    const surface = composer.querySelector(".composer");
    if (before?.sessionId === sessionId && before.welcome !== welcome) {
      const currentOffset = surface instanceof HTMLElement && animation.current?.playState === "running"
        ? new DOMMatrixReadOnly(getComputedStyle(surface).transform).m42
        : 0;
      animation.current?.cancel();
      if (surface instanceof HTMLElement && surface.animate
        && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        animation.current = surface.animate([
          { transform: `translateY(${before.top + currentOffset - top}px)` },
          { transform: "translateY(0)" },
        ], { duration: 380, easing: "cubic-bezier(0.22, 1, 0.36, 1)" });
      }
    } else if (before?.sessionId !== sessionId
      && !(before?.sessionId.startsWith("draft-") && !welcome)) {
      animation.current?.cancel();
    }
    previous.current = { sessionId, welcome, top };
  });

  useLayoutEffect(() => () => animation.current?.cancel(), []);

  return (
    <div className={`session-canvas${welcome ? " session-canvas--welcome" : ""}`}>
      <div className="session-welcome" aria-hidden={!welcome}>
        <WelcomeMessage />
      </div>
      {!welcome && <div className="session-canvas__feed">{feed}</div>}
      <div className="session-canvas__composer" ref={composerRef}>{children}</div>
    </div>
  );
}
