import { useEffect, useRef, useState } from "react";

import { Button } from "./Button.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

interface HandoffDialogProps {
  open: boolean;
  currentMode: "local" | "worktree";
  busy?: boolean;
  error?: string | undefined;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
  onConfirm: (target: "local" | "worktree") => void;
  onCancel: () => void;
}

export function HandoffDialog({
  open,
  currentMode,
  busy = false,
  error,
  getFallbackFocus,
  onConfirm,
  onCancel,
}: HandoffDialogProps) {
  const target = currentMode === "local" ? "worktree" : "local";
  const [selectedTarget, setSelectedTarget] = useState<"local" | "worktree">(target);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) setSelectedTarget(target);
  }, [open, target]);

  useDialogFocusLifecycle({
    open,
    initialFocusRef: confirmRef,
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

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel} aria-hidden={!open}>
      <div
        ref={dialogRef}
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="handoff-dialog-title"
        aria-describedby="handoff-dialog-description"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="handoff-dialog-title">Hand off this chat</h3>
          <p className="modal-subtitle" id="handoff-dialog-description">
            同一个 Session 会切换执行工作区。历史对话和 Checkpoint 不会改变。
          </p>
        </div>
        <div className="modal-body handoff-dialog-body">
          <fieldset>
            <legend>Move this chat to</legend>
            {(["local", "worktree"] as const).map((mode) => (
              <label key={mode} className="handoff-option">
                <input
                  type="radio"
                  name="handoff-target"
                  value={mode}
                  checked={selectedTarget === mode}
                  disabled={busy || mode === currentMode}
                  onChange={() => setSelectedTarget(mode)}
                />
                <span>{mode === "local" ? "Local project workspace" : "Managed Worktree"}</span>
                {mode === currentMode && <small>当前工作区</small>}
              </label>
            ))}
          </fieldset>
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>
        <div className="modal-footer">
          <Button variant="ghost" disabled={busy} onClick={onCancel}>Cancel</Button>
          <Button
            ref={confirmRef}
            variant="primary"
            loading={busy}
            disabled={busy || selectedTarget === currentMode}
            onClick={() => onConfirm(selectedTarget)}
          >
            Hand off
          </Button>
        </div>
      </div>
    </div>
  );
}
