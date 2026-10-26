import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";


import { CheckmarkIcon, ChevronRightIcon } from "./MenuIcons.js";
import { Tooltip } from "./Tooltip.js";

function renderPortal(children: ReactNode): ReactNode {
  if (typeof document === "undefined") {
    return children;
  }
  return createPortal(children, document.body);
}

export interface DropdownMenuItem {
  key: string;
  label: string;
  /** Optional icon displayed at the left of the item */
  icon?: ReactNode;
  /** Whether the item is checked (e.g. for single-select or branch options) */
  checked?: boolean;
  /** Whether to render a visual divider before this item */
  separator?: boolean;
  /** If true, renders with danger styling */
  danger?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  /** Optional submenu items */
  children?: DropdownMenuItem[];
}

interface DropdownMenuProps {
  /** Trigger element or text label */
  trigger: ReactNode;
  items: DropdownMenuItem[];
  /** Optional aria-label for the menu list */
  label?: string;
  className?: string;
  menuClassName?: string;
  title?: string;
  triggerAriaLabel?: string;
  triggerClassName?: string;
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
export function DropdownMenu({
  trigger,
  items,
  label,
  className = "",
  menuClassName = "",
  title,
  triggerAriaLabel,
  triggerClassName = "",
}: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [openSubmenuKey, setOpenSubmenuKey] = useState<string | undefined>(undefined);
  const [style, setStyle] = useState<CSSProperties>({});

  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

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
      const isInsideSubmenu = Boolean((event.target as HTMLElement | null)?.closest?.(".dropdown-submenu"));
      if (
        menuRef.current && !menuRef.current.contains(event.target as Node)
        && triggerRef.current && !triggerRef.current.contains(event.target as Node)
        && !isInsideSubmenu
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
      buttons[focusedIndex].focus({ preventScroll: true });
    }
  }, [focusedIndex, open]);

  function openWithIndex(initialIndex: number) {
    if (triggerRef.current) {
      const triggerRect = triggerRef.current.getBoundingClientRect();
      setStyle({
        position: "fixed",
        top: `${triggerRect.bottom + 4}px`,
        left: `${triggerRect.left}px`,
        zIndex: 9999,
      });
    }
    setFocusedIndex(initialIndex);
    setOpenSubmenuKey(undefined);
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
    const currentItem = items[focusedIndex];
    const currentHasChildren = Boolean(currentItem?.children && currentItem.children.length > 0);

    if (event.key === "Escape") {
      event.preventDefault();
      if (openSubmenuKey) {
        setOpenSubmenuKey(undefined);
      } else {
        setOpen(false);
        if (triggerRef.current && triggerRef.current.isConnected) {
          triggerRef.current.focus();
        }
      }
    } else if ((event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") && currentHasChildren && currentItem) {
      event.preventDefault();
      setOpenSubmenuKey(currentItem.key);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      const nextPos = (currentPos + 1) % enabledIndices.length;
      const nextIdx = enabledIndices[nextPos];
      if (nextIdx !== undefined) {
        setFocusedIndex(nextIdx);
        const nextItem = items[nextIdx];
        if (nextItem && nextItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      const prevPos = (currentPos - 1 + enabledIndices.length) % enabledIndices.length;
      const prevIdx = enabledIndices[prevPos];
      if (prevIdx !== undefined) {
        setFocusedIndex(prevIdx);
        const prevItem = items[prevIdx];
        if (prevItem && prevItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
    } else if (event.key === "Home") {
      event.preventDefault();
      const firstIdx = enabledIndices[0];
      if (firstIdx !== undefined) {
        setFocusedIndex(firstIdx);
        const firstItem = items[firstIdx];
        if (firstItem && firstItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
    } else if (event.key === "End") {
      event.preventDefault();
      const lastIdx = enabledIndices[enabledIndices.length - 1];
      if (lastIdx !== undefined) {
        setFocusedIndex(lastIdx);
        const lastItem = items[lastIdx];
        if (lastItem && lastItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div className={`dropdown-wrapper ${className}`.trim()}>
      <Tooltip content={title} disabled={open}>
        <button
          ref={triggerRef}
          type="button"
          className={`icon-button dropdown-trigger ${triggerClassName}`.trim()}
          aria-haspopup="menu"
          aria-expanded={open}
          title={title}
          aria-label={triggerAriaLabel}
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
      </Tooltip>

      {open && renderPortal(
        <div
          ref={menuRef}
          className={`dropdown-menu ${menuClassName}`.trim()}
          role="menu"
          aria-label={label}
          style={style}
          onKeyDown={handleMenuKeyDown}
        >
          {items.map((item, index) => {
            const hasChildren = Boolean(item.children && item.children.length > 0);
            const isSubmenuOpen = openSubmenuKey === item.key;
            return (
              <Fragment key={item.key}>
                {item.separator && <div className="dropdown-menu-separator" role="separator" aria-hidden="true" />}
                <button
                  ref={(el) => {
                    if (el) itemRefs.current.set(item.key, el);
                    else itemRefs.current.delete(item.key);
                  }}
                  role="menuitem"
                  type="button"
                  className={`dropdown-menu-item ${item.danger ? "danger-action" : ""}${item.checked ? " is-checked" : ""}${isSubmenuOpen ? " is-submenu-open" : ""}`.trim()}
                  disabled={item.disabled}
                  aria-haspopup={hasChildren ? "menu" : undefined}
                  aria-expanded={hasChildren ? isSubmenuOpen : undefined}
                  tabIndex={focusedIndex === index ? 0 : -1}
                  onPointerEnter={() => {
                    if (item.disabled) return;
                    setFocusedIndex(index);
                    if (hasChildren) {
                      setOpenSubmenuKey(item.key);
                    } else {
                      setOpenSubmenuKey(undefined);
                    }
                  }}
                  onClick={(event) => {
                    if (hasChildren) {
                      event.stopPropagation();
                      setOpenSubmenuKey((prev) => (prev === item.key ? undefined : item.key));
                      item.onClick?.();
                      return;
                    }
                    setOpen(false);
                    if (triggerRef.current && triggerRef.current.isConnected) {
                      triggerRef.current.focus();
                    }
                    item.onClick?.();
                  }}
                >
                  {item.icon && <span className="dropdown-menu-item__icon" aria-hidden="true">{item.icon}</span>}
                  <span className="dropdown-menu-item__label">{item.label}</span>
                  {hasChildren && (
                    <span className="dropdown-menu-item__chevron" aria-hidden="true">
                      <ChevronRightIcon />
                    </span>
                  )}
                  {item.checked && (
                    <span className="dropdown-menu-item__check" aria-hidden="true">
                      <CheckmarkIcon />
                    </span>
                  )}
                </button>
              </Fragment>
            );
          })}
          {(() => {
            const activeItem = items.find((it) => it.key === openSubmenuKey);
            if (!activeItem || !activeItem.children || activeItem.children.length === 0) return null;
            const anchor = itemRefs.current.get(activeItem.key) ?? null;
            return (
              <Submenu
                items={activeItem.children}
                parentAnchor={anchor}
                label={activeItem.label}
                onCloseAll={() => setOpen(false)}
                onCloseSubmenu={() => {
                  setOpenSubmenuKey(undefined);
                  anchor?.focus();
                }}
                restoreFocusElement={triggerRef.current}
              />
            );
          })()}
        </div>,
      )}
    </div>
  );
}

interface SubmenuProps {
  items: DropdownMenuItem[];
  parentAnchor: HTMLElement | null;
  label?: string;
  onCloseAll: () => void;
  onCloseSubmenu: () => void;
  restoreFocusElement?: HTMLElement | null | undefined;
}

function Submenu({
  items,
  parentAnchor,
  label,
  onCloseAll,
  onCloseSubmenu,
  restoreFocusElement,
}: SubmenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [style, setStyle] = useState<CSSProperties>({
    position: "fixed",
    left: "0px",
    top: "0px",
    zIndex: 10000,
  });

  const enabledIndices = items
    .map((item, index) => (!item.disabled ? index : -1))
    .filter((index) => index !== -1);

  useEffect(() => {
    const firstIdx = enabledIndices[0];
    if (firstIdx !== undefined) {
      setFocusedIndex(firstIdx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useLayoutEffect(() => {
    if (!menuRef.current || !parentAnchor) return;
    const parentRect = parentAnchor.getBoundingClientRect();
    const menuRect = menuRef.current.getBoundingClientRect();

    let left = parentRect.right - 2;
    let top = parentRect.top - 4;

    if (left + menuRect.width > window.innerWidth - 8) {
      left = Math.max(8, parentRect.left - menuRect.width + 2);
    }
    if (top + menuRect.height > window.innerHeight - 8) {
      top = Math.max(8, window.innerHeight - menuRect.height - 8);
    }
    if (top < 8) {
      top = 8;
    }

    setStyle({
      position: "fixed",
      left: `${left}px`,
      top: `${top}px`,
      zIndex: 10000,
    });
  }, [parentAnchor]);

  useEffect(() => {
    function handleWindowClose() {
      onCloseAll();
    }
    window.addEventListener("resize", handleWindowClose);
    window.addEventListener("scroll", handleWindowClose, true);
    return () => {
      window.removeEventListener("resize", handleWindowClose);
      window.removeEventListener("scroll", handleWindowClose, true);
    };
  }, [onCloseAll]);

  useEffect(() => {
    const buttons = menuRef.current?.querySelectorAll<HTMLElement>("[role=menuitem]");
    if (buttons && buttons[focusedIndex]) {
      buttons[focusedIndex].focus({ preventScroll: true });
    }
  }, [focusedIndex]);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    event.stopPropagation();
    if (enabledIndices.length === 0) return;
    const currentPos = enabledIndices.indexOf(focusedIndex);

    if (event.key === "ArrowDown") {
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
    } else if (event.key === "ArrowLeft" || event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onCloseSubmenu();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      const item = items[focusedIndex];
      if (item && !item.disabled) {
        onCloseAll();
        if (restoreFocusElement && restoreFocusElement.isConnected) {
          restoreFocusElement.focus();
        }
        item.onClick?.();
      }
    } else if (event.key === "Tab") {
      onCloseAll();
    }
  }

  return renderPortal(
    <div
      ref={menuRef}
      className="task-context-menu dropdown-submenu"
      role="menu"
      aria-label={label}
      style={style}
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={handleKeyDown}
    >
      {items.map((item, index) => (
        <Fragment key={item.key}>
          {item.separator && <div className="dropdown-menu-separator" role="separator" aria-hidden="true" />}
          <button
            role="menuitem"
            type="button"
            className={`dropdown-menu-item ${item.danger ? "danger-action" : ""}${item.checked ? " is-checked" : ""}`.trim()}
            disabled={item.disabled}
            tabIndex={focusedIndex === index ? 0 : -1}
            onPointerEnter={() => setFocusedIndex(index)}
            onClick={() => {
              onCloseAll();
              if (restoreFocusElement && restoreFocusElement.isConnected) {
                restoreFocusElement.focus();
              }
              item.onClick?.();
            }}
          >
            {item.icon && <span className="dropdown-menu-item__icon" aria-hidden="true">{item.icon}</span>}
            <span className="dropdown-menu-item__label">{item.label}</span>
            {item.checked && (
              <span className="dropdown-menu-item__check" aria-hidden="true">
                <CheckmarkIcon />
              </span>
            )}
          </button>
        </Fragment>
      ))}
    </div>,
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
  const [openSubmenuKey, setOpenSubmenuKey] = useState<string | undefined>(undefined);
  const itemRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
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
      const isInsideSubmenu = Boolean((event.target as HTMLElement | null)?.closest?.(".dropdown-submenu"));
      if (menuRef.current && !menuRef.current.contains(event.target as Node) && !isInsideSubmenu) {
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
      buttons[focusedIndex].focus({ preventScroll: true });
    }
  }, [focusedIndex]);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (enabledIndices.length === 0) return;

    const currentPos = enabledIndices.indexOf(focusedIndex);
    const currentItem = items[focusedIndex];
    const currentHasChildren = Boolean(currentItem?.children && currentItem.children.length > 0);

    if (event.key === "Escape") {
      event.preventDefault();
      if (openSubmenuKey) {
        setOpenSubmenuKey(undefined);
      } else {
        onClose();
        if (restoreFocusElement && restoreFocusElement.isConnected) {
          restoreFocusElement.focus();
        }
      }
    } else if ((event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") && currentHasChildren && currentItem) {
      event.preventDefault();
      setOpenSubmenuKey(currentItem.key);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      const nextPos = (currentPos + 1) % enabledIndices.length;
      const nextIdx = enabledIndices[nextPos];
      if (nextIdx !== undefined) {
        setFocusedIndex(nextIdx);
        const nextItem = items[nextIdx];
        if (nextItem && nextItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      const prevPos = (currentPos - 1 + enabledIndices.length) % enabledIndices.length;
      const prevIdx = enabledIndices[prevPos];
      if (prevIdx !== undefined) {
        setFocusedIndex(prevIdx);
        const prevItem = items[prevIdx];
        if (prevItem && prevItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
    } else if (event.key === "Home") {
      event.preventDefault();
      const firstIdx = enabledIndices[0];
      if (firstIdx !== undefined) {
        setFocusedIndex(firstIdx);
        const firstItem = items[firstIdx];
        if (firstItem && firstItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
    } else if (event.key === "End") {
      event.preventDefault();
      const lastIdx = enabledIndices[enabledIndices.length - 1];
      if (lastIdx !== undefined) {
        setFocusedIndex(lastIdx);
        const lastItem = items[lastIdx];
        if (lastItem && lastItem.key !== openSubmenuKey) {
          setOpenSubmenuKey(undefined);
        }
      }
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
      {items.map((item, index) => {
        const hasChildren = Boolean(item.children && item.children.length > 0);
        const isSubmenuOpen = openSubmenuKey === item.key;
        return (
          <Fragment key={item.key}>
            {item.separator && <div className="dropdown-menu-separator" role="separator" aria-hidden="true" />}
            <button
              ref={(el) => {
                if (el) itemRefs.current.set(item.key, el);
                else itemRefs.current.delete(item.key);
              }}
              role="menuitem"
              type="button"
              className={`dropdown-menu-item ${item.danger ? "danger-action" : ""}${item.checked ? " is-checked" : ""}${isSubmenuOpen ? " is-submenu-open" : ""}`.trim()}
              disabled={item.disabled}
              aria-haspopup={hasChildren ? "menu" : undefined}
              aria-expanded={hasChildren ? isSubmenuOpen : undefined}
              tabIndex={focusedIndex === index ? 0 : -1}
              onPointerEnter={() => {
                if (item.disabled) return;
                setFocusedIndex(index);
                if (hasChildren) {
                  setOpenSubmenuKey(item.key);
                } else {
                  setOpenSubmenuKey(undefined);
                }
              }}
              onClick={(event) => {
                if (hasChildren) {
                  event.stopPropagation();
                  setOpenSubmenuKey((prev) => (prev === item.key ? undefined : item.key));
                  item.onClick?.();
                  return;
                }
                onClose();
                if (restoreFocusElement && restoreFocusElement.isConnected) {
                  restoreFocusElement.focus();
                }
                item.onClick?.();
              }}
            >
              {item.icon && <span className="dropdown-menu-item__icon" aria-hidden="true">{item.icon}</span>}
              <span className="dropdown-menu-item__label">{item.label}</span>
              {hasChildren && (
                <span className="dropdown-menu-item__chevron" aria-hidden="true">
                  <ChevronRightIcon />
                </span>
              )}
              {item.checked && (
                <span className="dropdown-menu-item__check" aria-hidden="true">
                  <CheckmarkIcon />
                </span>
              )}
            </button>
          </Fragment>
        );
      })}
      {(() => {
        const activeItem = items.find((it) => it.key === openSubmenuKey);
        if (!activeItem || !activeItem.children || activeItem.children.length === 0) return null;
        const anchor = itemRefs.current.get(activeItem.key) ?? null;
        return (
          <Submenu
            items={activeItem.children}
            parentAnchor={anchor}
            label={activeItem.label}
            onCloseAll={onClose}
            onCloseSubmenu={() => {
              setOpenSubmenuKey(undefined);
              anchor?.focus();
            }}
            restoreFocusElement={restoreFocusElement}
          />
        );
      })()}
    </div>,
  );
}
