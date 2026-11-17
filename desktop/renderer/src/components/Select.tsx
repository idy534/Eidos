import {
  forwardRef,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type MutableRefObject,
  type Ref,
  type SelectHTMLAttributes,
} from "react";
import { isWindowsPlatform } from "../platform.js";

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

function isWindowsSelectEnabled(): boolean {
  if (isWindowsPlatform()) return true;
  return typeof document !== "undefined" && document.documentElement?.dataset.platform === "windows";
}

function hasOpenSelect(): boolean {
  if (typeof document === "undefined") return false;
  try {
    return Boolean(document.querySelector("select:open"));
  } catch {
    return false;
  }
}

let activeWindowsSelectCount = 0;

function handleWindowsSelectEscape(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !hasOpenSelect()) return;
  // Let Chromium dismiss the top-layer select picker without propagating Escape to parent modals.
  event.stopPropagation();
}

function retainEscapeGuard(): () => void {
  if (typeof window === "undefined") return () => {};
  if (activeWindowsSelectCount === 0) {
    window.addEventListener("keydown", handleWindowsSelectEscape, true);
  }
  activeWindowsSelectCount += 1;
  return () => {
    activeWindowsSelectCount = Math.max(0, activeWindowsSelectCount - 1);
    if (activeWindowsSelectCount === 0) {
      window.removeEventListener("keydown", handleWindowsSelectEscape, true);
    }
  };
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null): void {
  if (!ref) return;
  if (typeof ref === "function") {
    ref(value);
  } else {
    (ref as MutableRefObject<T | null>).current = value;
  }
}

/**
 * Shared Select primitive.
 *
 * Preserves native `<select>` DOM and popup behavior on macOS while enabling
 * Chromium's customizable `base-select` trigger slot (`<button><selectedcontent />`)
 * and modal-safe `Escape` handling on Windows.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { children, ...props },
  forwardedRef,
) {
  const selectRef = useRef<HTMLSelectElement | null>(null);
  const slotRef = useRef<HTMLButtonElement | null>(null);
  const windows = isWindowsSelectEnabled();

  const setRef = useCallback(
    (node: HTMLSelectElement | null) => {
      selectRef.current = node;
      assignRef(forwardedRef, node);
    },
    [forwardedRef],
  );

  useEffect(() => {
    if (!windows) return;
    return retainEscapeGuard();
  }, [windows]);

  // Attach the customizable select slot imperatively so React 19's DOM nesting
  // validator does not emit dev-mode warnings for <button>/<selectedcontent> inside <select>.
  useLayoutEffect(() => {
    const select = selectRef.current;
    if (!select) return;
    if (!windows) {
      if (slotRef.current?.parentNode === select) {
        slotRef.current.remove();
      }
      slotRef.current = null;
      return;
    }

    let slot = slotRef.current;
    if (!slot) {
      slot = document.createElement("button");
      slot.type = "button";
      slot.tabIndex = -1;
      slot.setAttribute("aria-hidden", "true");
      slot.setAttribute("data-eidos-select-slot", "true");
      slot.appendChild(document.createElement("selectedcontent"));
      slotRef.current = slot;
    }
    if (select.firstElementChild !== slot) {
      select.prepend(slot);
    }
  });

  useLayoutEffect(() => {
    return () => {
      slotRef.current?.remove();
      slotRef.current = null;
    };
  }, []);

  return (
    <select ref={setRef} {...props}>
      {children}
    </select>
  );
});
