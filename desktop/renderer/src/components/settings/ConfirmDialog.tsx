import React, { useEffect, useRef } from "react";

import { Button } from "../Button.js";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** If true, renders confirm button with danger variant and focuses cancel by default. */
  isDestructive?: boolean;
  busy?: boolean;
  error?: string | undefined;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Accessible confirm dialog.
 *
 * Keyboard behaviour:
 * - Opens focused on Cancel (if destructive) or Confirm otherwise.
 * - Tab/Shift+Tab cycle only within the dialog (focus trap).
 * - Escape calls onCancel.
 * - Backdrop click calls onCancel (but not for destructive dialogs — they
 *   require explicit button interaction to prevent accidental dismiss).
 * - Closes focus back to the element that triggered the dialog.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认",
  cancelLabel = "取消",
  isDestructive = false,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  // Store the element that opened the dialog, restore focus on close
  useEffect(() => {
    if (open) {
      triggerRef.current = document.activeElement as HTMLElement;
      // Focus initial element after render
      const timer = setTimeout(() => {
        if (isDestructive) {
          cancelBtnRef.current?.focus();
        } else {
          confirmBtnRef.current?.focus();
        }
      }, 16);
      return () => clearTimeout(timer);
    } else {
      // Return focus to the element that triggered the dialog
      triggerRef.current?.focus();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Escape key closes the dialog when not busy
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busy) {
          onCancel();
        }
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, busy, onCancel]);

  // Focus trap: keep Tab cycles inside the dialog
  useEffect(() => {
    if (!open) return;
    function handleFocusTrap(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;

      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey) {
        if (active === first) {
          event.preventDefault();
          last?.focus();
        }
      } else {
        if (active === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    }
    window.addEventListener("keydown", handleFocusTrap);
    return () => window.removeEventListener("keydown", handleFocusTrap);
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      // Destructive dialogs don't close on backdrop click to prevent accidents
      onClick={isDestructive ? undefined : onCancel}
      aria-hidden={!open}
    >
      <div
        ref={dialogRef}
        className="modal-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-desc"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="confirm-dialog-title">{title}</h3>
        </div>
        <div id="confirm-dialog-desc" className="modal-body">
          {typeof description === "string" ? <p>{description}</p> : description}
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>
        <div className="modal-footer">
          <Button
            ref={cancelBtnRef}
            variant="ghost"
            size="medium"
            disabled={busy}
            onClick={onCancel}
          >
            {cancelLabel}
          </Button>
          <Button
            ref={confirmBtnRef}
            variant={isDestructive ? "danger" : "primary"}
            size="medium"
            disabled={busy}
            loading={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
