import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

export interface DropdownMenuItem {
  key: string;
  label: string;
  /** If true, renders with danger styling */
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

interface DropdownMenuProps {
  /** Trigger element */
  trigger: ReactNode;
  items: DropdownMenuItem[];
  /** Optional aria-label for the menu list */
  label?: string;
  /** Anchor position; defaults to automatic from trigger */
  position?: { x: number; y: number };
}

/**
 * Accessible dropdown menu with keyboard navigation.
 *
 * Keyboard support:
 * - ArrowDown/ArrowUp: navigate items
 * - Enter/Space: activate item
 * - Escape: close menu, return focus to trigger
 * - Tab: close menu
 */
export function DropdownMenu({ trigger, items, label }: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on outside click
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
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  // Focus first item when opened
  useEffect(() => {
    if (open) {
      setFocusedIndex(0);
      // Focus is managed via tabIndex on the menu items
      const firstItem = menuRef.current?.querySelector<HTMLElement>("[role=menuitem]");
      firstItem?.focus();
    }
  }, [open]);

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  function handleMenuKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const enabledItems = items.filter((item) => !item.disabled);
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setFocusedIndex((prev) => {
        const next = (prev + 1) % enabledItems.length;
        return next;
      });
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setFocusedIndex((prev) => {
        const next = (prev - 1 + enabledItems.length) % enabledItems.length;
        return next;
      });
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  }

  // Focus the active item when focusedIndex changes
  useEffect(() => {
    if (!open) return;
    const enabledButtons = menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not([disabled])");
    enabledButtons?.[focusedIndex]?.focus();
  }, [focusedIndex, open]);

  return (
    <div className="dropdown-wrapper">
      <button
        ref={triggerRef}
        type="button"
        className="icon-button dropdown-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleTriggerKeyDown}
      >
        {trigger}
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          className="dropdown-menu"
          role="menu"
          aria-label={label}
          onKeyDown={handleMenuKeyDown}
        >
          {items.map((item) => (
            <button
              key={item.key}
              role="menuitem"
              type="button"
              className={item.danger ? "danger-action" : ""}
              disabled={item.disabled}
              tabIndex={-1}
              onClick={() => {
                setOpen(false);
                triggerRef.current?.focus();
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}

/**
 * A context menu opened at a fixed (x, y) position.
 * Provides keyboard navigation and Escape-to-close.
 */
export function ContextMenu({
  items,
  x,
  y,
  label,
  onClose,
}: {
  items: DropdownMenuItem[];
  x: number;
  y: number;
  label?: string;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);

  useEffect(() => {
    const firstItem = menuRef.current?.querySelector<HTMLElement>("[role=menuitem]");
    firstItem?.focus();
  }, []);

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    }
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [onClose]);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const enabledItems = items.filter((item) => !item.disabled);
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setFocusedIndex((prev) => (prev + 1) % enabledItems.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setFocusedIndex((prev) => (prev - 1 + enabledItems.length) % enabledItems.length);
    } else if (event.key === "Tab") {
      onClose();
    }
  }

  useEffect(() => {
    const enabledButtons = menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not([disabled])");
    enabledButtons?.[focusedIndex]?.focus();
  }, [focusedIndex]);

  return createPortal(
    <div
      ref={menuRef}
      className="task-context-menu"
      role="menu"
      aria-label={label}
      style={{ left: x, top: y }}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={handleKeyDown}
    >
      {items.map((item) => (
        <button
          key={item.key}
          role="menuitem"
          type="button"
          className={item.danger ? "danger-action" : ""}
          disabled={item.disabled}
          tabIndex={-1}
          onClick={() => {
            onClose();
            item.onClick();
          }}
        >
          {item.label}
        </button>
      ))}
    </div>,
    document.body,
  );
}
