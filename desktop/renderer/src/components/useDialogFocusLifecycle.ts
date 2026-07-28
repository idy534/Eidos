import { useLayoutEffect, useRef } from "react";
import type { RefObject } from "react";

interface DialogFocusLifecycleOptions {
  open: boolean;
  initialFocusRef: RefObject<HTMLElement | null>;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
}

export function useDialogFocusLifecycle({
  open,
  initialFocusRef,
  getFallbackFocus,
}: DialogFocusLifecycleOptions): void {
  const wasOpenRef = useRef(false);
  const triggerRef = useRef<HTMLElement | null>(null);
  const rafRef = useRef<number | null>(null);

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
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        const target = initialFocusRef.current;
        if (target?.isConnected) target.focus();
      });
    } else if (wasOpen && !open) {
      const target = triggerRef.current?.isConnected
        ? triggerRef.current
        : getFallbackFocus?.();
      if (target?.isConnected) target.focus();
      triggerRef.current = null;
    }

    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [open, initialFocusRef, getFallbackFocus]);
}
