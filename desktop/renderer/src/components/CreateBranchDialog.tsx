import { useEffect, useRef, useState } from "react";

import { Button } from "./Button.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

interface CreateBranchDialogProps {
  open: boolean;
  busy?: boolean;
  error?: string | undefined;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
  onConfirm: (branch: string) => void;
  onCancel: () => void;
}

export function CreateBranchDialog({
  open,
  busy = false,
  error,
  getFallbackFocus,
  onConfirm,
  onCancel,
}: CreateBranchDialogProps) {
  const [branch, setBranch] = useState("feature/");
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) setBranch("feature/");
  }, [open]);

  useDialogFocusLifecycle({
    open,
    initialFocusRef: inputRef,
    getFallbackFocus,
  });

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, onCancel, open]);

  useEffect(() => {
    if (!open) return;
    const handleFocusTrap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleFocusTrap);
    return () => window.removeEventListener("keydown", handleFocusTrap);
  }, [open]);

  if (!open) return null;
  const trimmed = branch.trim();

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel} aria-hidden={!open}>
      <div
        ref={dialogRef}
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-branch-dialog-title"
        aria-describedby="create-branch-dialog-description"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="create-branch-dialog-title">Create Branch</h3>
          <p className="modal-subtitle" id="create-branch-dialog-description">
            在当前 detached Worktree 上创建分支。
          </p>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (trimmed) onConfirm(trimmed);
          }}
        >
          <div className="modal-body create-branch-dialog-body">
            <label className="create-session-ref-field" htmlFor="create-branch-name">
              <span>Branch name</span>
              <input
                ref={inputRef}
                id="create-branch-name"
                value={branch}
                maxLength={4096}
                onChange={(event) => setBranch(event.target.value)}
                disabled={busy}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            {error && <p className="setting-field-error" role="alert">{error}</p>}
          </div>
          <div className="modal-footer">
            <Button ref={cancelButtonRef} variant="ghost" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={busy} disabled={!trimmed}>
              Create
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
