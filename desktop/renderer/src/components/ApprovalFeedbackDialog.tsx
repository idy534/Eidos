import { useEffect, useRef, useState } from "react";
import { Button } from "./Button.js";
import type { ApprovalRequest } from "../contracts.js";

const MAX_FEEDBACK_BYTES = 2_000;

interface ApprovalFeedbackDialogProps {
  /** The approval being rejected. null means the dialog is closed. */
  approval: ApprovalRequest | null;
  /** True while the reject IPC call is in-flight */
  busy?: boolean | undefined;
  /** Error from the last submission attempt */
  error?: string | undefined;
  onConfirm: (request: ApprovalRequest, feedback: string) => void;
  onCancel: () => void;
}

function utf8ByteLength(str: string): number {
  return new TextEncoder().encode(str).byteLength;
}

/**
 * Dialog shown when the user clicks "拒绝" on an Approval card.
 *
 * Behaviour:
 * - Feedback is optional (may submit with empty string).
 * - Validate that feedback does not exceed 2000 UTF-8 bytes.
 * - Submit button is disabled when over the byte limit.
 * - Escape cancels the dialog.
 * - Focus is trapped inside the dialog.
 * - On open, focus goes to the textarea.
 * - On close, focus returns to the trigger element.
 */
export function ApprovalFeedbackDialog({
  approval,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: ApprovalFeedbackDialogProps) {
  const [feedback, setFeedback] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  const byteLength = utf8ByteLength(feedback);
  const overLimit = byteLength > MAX_FEEDBACK_BYTES;
  const isOpen = approval !== null;

  // Store trigger, focus textarea on open, restore on close
  useEffect(() => {
    if (isOpen) {
      triggerRef.current = document.activeElement as HTMLElement;
      setFeedback(""); // reset on each open
      setTimeout(() => textareaRef.current?.focus(), 16);
    } else {
      triggerRef.current?.focus();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Escape closes the dialog
  useEffect(() => {
    if (!isOpen) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onCancel]);

  // Focus trap
  useEffect(() => {
    if (!isOpen) return;
    function handleFocusTrap(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey) {
        if (active === first) { event.preventDefault(); last?.focus(); }
      } else {
        if (active === last) { event.preventDefault(); first?.focus(); }
      }
    }
    window.addEventListener("keydown", handleFocusTrap);
    return () => window.removeEventListener("keydown", handleFocusTrap);
  }, [isOpen]);

  if (!isOpen || !approval) return null;

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        ref={dialogRef}
        className="modal-dialog approval-feedback-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="feedback-dialog-title"
        aria-describedby="feedback-dialog-desc"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="feedback-dialog-title">为什么拒绝这次操作？</h3>
        </div>
        <div id="feedback-dialog-desc" className="modal-body">
          <p className="approval-reject-summary">{approval.summary}</p>
          <label className="sr-only" htmlFor="approval-feedback">
            拒绝原因（可选）
          </label>
          <textarea
            ref={textareaRef}
            id="approval-feedback"
            className="approval-feedback-input"
            rows={4}
            placeholder="可选：说明拒绝原因，Agent 将参考此反馈（可留空）"
            value={feedback}
            disabled={busy}
            onChange={(e) => setFeedback(e.target.value)}
          />
          <div className="approval-feedback-meta">
            {overLimit ? (
              <span className="approval-feedback-overcount" role="alert">
                超出限制 {byteLength - MAX_FEEDBACK_BYTES} 字节
              </span>
            ) : (
              <span className="approval-feedback-remaining">
                {MAX_FEEDBACK_BYTES - byteLength} 字节剩余
              </span>
            )}
          </div>
          {error && (
            <p className="setting-field-error" role="alert">{error}</p>
          )}
        </div>
        <div className="modal-footer">
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
            variant="danger"
            size="medium"
            disabled={busy || overLimit}
            loading={busy}
            onClick={() => onConfirm(approval, feedback)}
          >
            拒绝并反馈
          </Button>
        </div>
      </div>
    </div>
  );
}
