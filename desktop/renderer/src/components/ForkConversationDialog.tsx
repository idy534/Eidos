import { useEffect, useRef } from "react";
import type { Session } from "../../../shared/domain-contracts.js";
import { Button } from "./Button.js";
import { ToastContainer } from "./ToastContainer.js";
import { ForkIcon } from "./ForkIcon.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

interface ForkConversationDialogProps {
  open: boolean;
  session?: Session | undefined;
  forkTarget: "same_workspace" | "new_worktree";
  busy?: boolean;
  error?: string | undefined;
  onTargetChange: (target: "same_workspace" | "new_worktree") => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ForkConversationDialog({
  open,
  session,
  forkTarget,
  busy = false,
  error,
  onTargetChange,
  onConfirm,
  onCancel,
}: ForkConversationDialogProps) {
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useDialogFocusLifecycle({ open, initialFocusRef: confirmBtnRef });

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busy) onCancel();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, busy, onCancel]);

  useEffect(() => {
    if (!open) return;
    function handleFocusTrap(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;

      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])",
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

  const isWorktreeOnly = session?.executionMode === "worktree";

  return (
    <div
      className="modal-backdrop"
      onClick={busy ? undefined : onCancel}
      aria-hidden={!open}
    >
      {error && (
        <ToastContainer
          toasts={[{ id: `fork-dialog-error-${error}`, message: error, type: "error", duration: 4000 }]}
        />
      )}
      <div
        ref={dialogRef}
        className="modal-dialog fork-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="fork-dialog-title"
        aria-describedby="fork-dialog-description"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header fork-dialog-header">
          <div className="fork-dialog-header-icon" aria-hidden="true">
            <ForkIcon size={18} />
          </div>
          <div className="fork-dialog-header-text">
            <h3 id="fork-dialog-title">从这里分叉</h3>
            <p className="modal-subtitle" id="fork-dialog-description">
              新会话会保留截至这次回答的聊天。工作区使用当前文件状态。
            </p>
          </div>
        </div>

        <div className="modal-body fork-dialog-body">
          <fieldset className="fork-dialog-fieldset" disabled={busy}>
            <legend className="fork-dialog-legend">目标工作区模式</legend>
            <div className="fork-dialog-options">
              {!isWorktreeOnly && (
                <label
                  className={`fork-mode-card${forkTarget === "same_workspace" ? " is-selected" : ""}${busy ? " is-disabled" : ""}`}
                >
                  <input
                    type="radio"
                    name="fork-target"
                    value="same_workspace"
                    checked={forkTarget === "same_workspace"}
                    disabled={busy}
                    onChange={() => onTargetChange("same_workspace")}
                    className="fork-radio-input"
                    aria-label="在当前目录继续，两条会话共享文件。"
                  />
                  <span className="fork-radio-indicator" aria-hidden="true">
                    <span className="fork-radio-dot" />
                  </span>
                  <div className="fork-card-content">
                    <div className="fork-card-header">
                      <span className="fork-card-icon" aria-hidden="true">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="2" y="3" width="20" height="14" rx="2" />
                          <path d="M8 21h8" />
                          <path d="M12 17v4" />
                        </svg>
                      </span>
                      <strong className="fork-card-title">当前目录</strong>
                      <span className="fork-card-badge">共享文件</span>
                    </div>
                    <small className="fork-card-desc">在当前目录继续，两条会话共享文件。</small>
                  </div>
                </label>
              )}

              <label
                className={`fork-mode-card${forkTarget === "new_worktree" ? " is-selected" : ""}${busy ? " is-disabled" : ""}`}
              >
                <input
                  type="radio"
                  name="fork-target"
                  value="new_worktree"
                  checked={forkTarget === "new_worktree"}
                  disabled={busy}
                  onChange={() => onTargetChange("new_worktree")}
                  className="fork-radio-input"
                  aria-label="在独立工作树继续。"
                />
                <span className="fork-radio-indicator" aria-hidden="true">
                  <span className="fork-radio-dot" />
                </span>
                <div className="fork-card-content">
                  <div className="fork-card-header">
                    <span className="fork-card-icon" aria-hidden="true">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="6" y1="3" x2="6" y2="15" />
                        <circle cx="18" cy="6" r="3" />
                        <circle cx="6" cy="18" r="3" />
                        <path d="M18 9a9 9 0 0 1-9 9" />
                      </svg>
                    </span>
                    <strong className="fork-card-title">独立工作树</strong>
                    <span className="fork-card-badge fork-card-badge--accent">隔离分支</span>
                  </div>
                  <small className="fork-card-desc">在独立工作树继续。</small>
                </div>
              </label>
            </div>
          </fieldset>
        </div>

        <div className="modal-footer fork-dialog-footer">
          <Button
            ref={cancelBtnRef}
            variant="ghost"
            size="medium"
            disabled={busy}
            onClick={onCancel}
          >
            取消
          </Button>
          <Button
            ref={confirmBtnRef}
            variant="primary"
            size="medium"
            disabled={busy}
            loading={busy}
            onClick={onConfirm}
          >
            创建分叉
          </Button>
        </div>
      </div>
    </div>
  );
}
