import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";


function renderPortal(children: ReactNode): ReactNode {
  if (typeof document === "undefined") {
    return children;
  }
  return createPortal(children, document.body);
}

export interface DropdownMenuItem {
  key: string;
  label: string;
  /** If true, renders with danger styling */
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

interface DropdownMenuProps {
  /** Trigger element or text label */
  trigger: ReactNode;
  items: DropdownMenuItem[];
  /** Optional aria-label for the menu list */
  label?: string;
  className?: string;
}

/**
 * Accessible Dropdown Menu primitive.
 *
 * Keyboard support:
 * - ArrowDown: opens menu & focuses first item (or moves to next enabled item)
 * - ArrowUp: opens menu & focuses last item (or moves to previous enabled item)
 * - Home / End: jumps to first / last enabled item
 * - Enter / Space: activates item
 * - Escape: closes menu and restores trigger focus
 * - Tab: closes menu
 */
export function DropdownMenu({ trigger, items, label, className = "" }: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [style, setStyle] = useState<CSSProperties>({});

  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const enabledIndices = items
    .map((item, index) => (!item.disabled ? index : -1))
    .filter((index) => index !== -1);

  // Position portal dropdown relative to trigger with collision detection
  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !menuRef.current) return;

    const triggerRect = triggerRef.current.getBoundingClientRect();
    const menuRect = menuRef.current.getBoundingClientRect();

    let top = triggerRect.bottom + 4;
    let left = triggerRect.left;

    // Collision check bottom edge
    if (top + menuRect.height > window.innerHeight - 8) {
      top = Math.max(8, triggerRect.top - menuRect.height - 4);
    }
    // Collision check right edge
    if (left + menuRect.width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - menuRect.width - 8);
    }

    setStyle({
      position: "fixed",
      top: `${top}px`,
      left: `${left}px`,
      zIndex: 9999,
    });
  }, [open]);

  // Close on outside click, window resize, or scroll
  useEffect(() => {
    if (!open) return;

    function handlePointerDown(event: PointerEvent) {
      if (
        menuRef.current && !menuRef.current.contains(event.target as Node)
        && triggerRef.current && !triggerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }

    function handleClose() {
      setOpen(false);
    }

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("resize", handleClose);
    window.addEventListener("scroll", handleClose, true);

    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("resize", handleClose);
      window.removeEventListener("scroll", handleClose, true);
    };
  }, [open]);

  // Sync focus to focusedIndex item when opened or index changes
  useEffect(() => {
    if (!open) return;
    const buttons = menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]");
    if (buttons && buttons[focusedIndex]) {
      buttons[focusedIndex].focus();
    }
  }, [focusedIndex, open]);

  function openWithIndex(initialIndex: number) {
    setFocusedIndex(initialIndex);
    setOpen(true);
  }

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (enabledIndices.length === 0) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      openWithIndex(enabledIndices[0] ?? 0);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openWithIndex(enabledIndices[enabledIndices.length - 1] ?? 0);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openWithIndex(enabledIndices[0] ?? 0);
    }
  }

  function handleMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (enabledIndices.length === 0) return;

    const currentPos = enabledIndices.indexOf(focusedIndex);

    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      if (triggerRef.current && triggerRef.current.isConnected) {
        triggerRef.current.focus();
      }
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      const nextPos = (currentPos + 1) % enabledIndices.length;
      const nextIdx = enabledIndices[nextPos];
      if (nextIdx !== undefined) setFocusedIndex(nextIdx);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      const prevPos = (currentPos - 1 + enabledIndices.length) % enabledIndices.length;
      const prevIdx = enabledIndices[prevPos];
      if (prevIdx !== undefined) setFocusedIndex(prevIdx);
    } else if (event.key === "Home") {
      event.preventDefault();
      const firstIdx = enabledIndices[0];
      if (firstIdx !== undefined) setFocusedIndex(firstIdx);
    } else if (event.key === "End") {
      event.preventDefault();
      const lastIdx = enabledIndices[enabledIndices.length - 1];
      if (lastIdx !== undefined) setFocusedIndex(lastIdx);
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div className={`dropdown-wrapper ${className}`.trim()}>
      <button
        ref={triggerRef}
        type="button"
        className="icon-button dropdown-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          if (open) {
            setOpen(false);
          } else {
            openWithIndex(enabledIndices[0] ?? 0);
          }
        }}
        onKeyDown={handleTriggerKeyDown}
      >
        {trigger}
      </button>

      {open && renderPortal(
        <div
          ref={menuRef}
          className="dropdown-menu"
          role="menu"
          aria-label={label}
          style={style}
          onKeyDown={handleMenuKeyDown}
        >
          {items.map((item, index) => (
            <button
              key={item.key}
              role="menuitem"
              type="button"
              className={item.danger ? "danger-action" : ""}
              disabled={item.disabled}
              tabIndex={focusedIndex === index ? 0 : -1}
              onClick={() => {
                setOpen(false);
                if (triggerRef.current && triggerRef.current.isConnected) {
                  triggerRef.current.focus();
                }
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>,
      )}
    </div>
  );
}

/**
 * A Context Menu opened at fixed screen (x, y) coordinates with collision handling.
 * Supports keyboard navigation, Escape to close, and focus restoration to the trigger element.
 */
export function ContextMenu({
  items,
  x,
  y,
  label,
  restoreFocusElement,
  onClose,
}: {
  items: DropdownMenuItem[];
  x: number;
  y: number;
  label?: string;
  restoreFocusElement?: HTMLElement | null | undefined;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [style, setStyle] = useState<CSSProperties>({
    position: "fixed",
    left: `${x}px`,
    top: `${y}px`,
    zIndex: 9999,
  });

  const enabledIndices = items
    .map((item, index) => (!item.disabled ? index : -1))
    .filter((index) => index !== -1);

  // Focus initial enabled item
  useEffect(() => {
    const firstIdx = enabledIndices[0];
    if (firstIdx !== undefined) {
      setFocusedIndex(firstIdx);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Collision handling for right and bottom viewport bounds
  useLayoutEffect(() => {
    if (!menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();

    let finalLeft = x;
    let finalTop = y;

    if (x + rect.width > window.innerWidth - 8) {
      finalLeft = Math.max(8, window.innerWidth - rect.width - 8);
    }
    if (y + rect.height > window.innerHeight - 8) {
      finalTop = Math.max(8, window.innerHeight - rect.height - 8);
    }

    setStyle({
      position: "fixed",
      left: `${finalLeft}px`,
      top: `${finalTop}px`,
      zIndex: 9999,
    });
  }, [x, y]);

  // Close on outside pointer, window resize, or scroll
  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    }

    function handleWindowClose() {
      onClose();
    }

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("resize", handleWindowClose);
    window.addEventListener("scroll", handleWindowClose, true);

    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("resize", handleWindowClose);
      window.removeEventListener("scroll", handleWindowClose, true);
    };
  }, [onClose]);

  // Synchronize focus to focusedIndex item
  useEffect(() => {
    const buttons = menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]");
    if (buttons && buttons[focusedIndex]) {
      buttons[focusedIndex].focus();
    }
  }, [focusedIndex]);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (enabledIndices.length === 0) return;

    const currentPos = enabledIndices.indexOf(focusedIndex);

    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      if (restoreFocusElement && restoreFocusElement.isConnected) {
        restoreFocusElement.focus();
      }
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      const nextPos = (currentPos + 1) % enabledIndices.length;
      const nextIdx = enabledIndices[nextPos];
      if (nextIdx !== undefined) setFocusedIndex(nextIdx);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      const prevPos = (currentPos - 1 + enabledIndices.length) % enabledIndices.length;
      const prevIdx = enabledIndices[prevPos];
      if (prevIdx !== undefined) setFocusedIndex(prevIdx);
    } else if (event.key === "Home") {
      event.preventDefault();
      const firstIdx = enabledIndices[0];
      if (firstIdx !== undefined) setFocusedIndex(firstIdx);
    } else if (event.key === "End") {
      event.preventDefault();
      const lastIdx = enabledIndices[enabledIndices.length - 1];
      if (lastIdx !== undefined) setFocusedIndex(lastIdx);
    } else if (event.key === "Tab") {
      onClose();
    }
  }

  return renderPortal(
    <div
      ref={menuRef}
      className="task-context-menu"
      role="menu"
      aria-label={label}
      style={style}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={handleKeyDown}
    >
      {items.map((item, index) => (
        <button
          key={item.key}
          role="menuitem"
          type="button"
          className={item.danger ? "danger-action" : ""}
          disabled={item.disabled}
          tabIndex={focusedIndex === index ? 0 : -1}
          onClick={() => {
            onClose();
            if (restoreFocusElement && restoreFocusElement.isConnected) {
              restoreFocusElement.focus();
            }
            item.onClick();
          }}
        >
          {item.label}
        </button>
      ))}
    </div>,
  );
}
