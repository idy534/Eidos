import { useLayoutEffect, useRef } from "react";
import type { RefObject } from "react";

interface DialogFocusLifecycleOptions {
  open: boolean;
  initialFocusRef?: RefObject<HTMLElement | null> | undefined;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
}

function restoreFocusTarget(target: HTMLElement | null | undefined): void {
  if (!target?.isConnected) return;
  target.setAttribute("data-restored-focus", "true");
  target.focus();
  if (typeof queueMicrotask === "function") {
    queueMicrotask(() => {
      if (target.isConnected) {
        target.removeAttribute("data-restored-focus");
      }
    });
  } else {
    target.removeAttribute("data-restored-focus");
  }
}

export function useDialogFocusLifecycle({
  open,
  initialFocusRef,
  getFallbackFocus,
}: DialogFocusLifecycleOptions): void {
  const wasOpenRef = useRef(false);
  const triggerRef = useRef<HTMLElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const fallbackRef = useRef(getFallbackFocus);
  fallbackRef.current = getFallbackFocus;

  useLayoutEffect(() => {
    const wasOpen = wasOpenRef.current;
    wasOpenRef.current = open;
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (!wasOpen && open) {
      const active = document.activeElement;
      triggerRef.current = active instanceof HTMLElement && active.isConnected
        ? active
        : null;
      if (initialFocusRef) {
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = null;
          const currentActive = document.activeElement;
          if (currentActive && currentActive !== document.body && currentActive !== triggerRef.current) {
            return;
          }
          const target = initialFocusRef.current;
          if (target?.isConnected) target.focus();
        });
      }
    } else if (wasOpen && !open) {
      const target = triggerRef.current?.isConnected
        ? triggerRef.current
        : getFallbackFocus?.();
      restoreFocusTarget(target);
      triggerRef.current = null;
    }

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [open, initialFocusRef, getFallbackFocus]);

  useLayoutEffect(() => {
    return () => {
      if (!wasOpenRef.current) return;
      wasOpenRef.current = false;
      const trigger = triggerRef.current;
      triggerRef.current = null;
      const target = trigger && trigger !== document.body && trigger.isConnected
        ? trigger
        : fallbackRef.current?.();
      restoreFocusTarget(target);
    };
  }, []);
}
