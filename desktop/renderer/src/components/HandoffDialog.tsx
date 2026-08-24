import { useEffect, useRef, useState } from "react";

import { Button } from "./Button.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

interface HandoffDialogProps {
  open: boolean;
  currentMode: "local" | "worktree";
  currentBranch?: string | null;
  branches?: readonly string[];
  associatedWorktreeId?: string | undefined;
  changedFileCount?: number;
  busy?: boolean;
  error?: string | undefined;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
  onConfirm: (target: "local" | "worktree", branch?: string) => void;
  onCancel: () => void;
}

export function HandoffDialog({
  open,
  currentMode,
  currentBranch = null,
  branches = [],
  associatedWorktreeId,
  changedFileCount = 0,
  busy = false,
  error,
  getFallbackFocus,
  onConfirm,
  onCancel,
}: HandoffDialogProps) {
  const target = currentMode === "local" ? "worktree" : "local";
  const [selectedTarget, setSelectedTarget] = useState<"local" | "worktree">(target);
  const [selectedBranch, setSelectedBranch] = useState(currentBranch ?? "");
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setSelectedTarget(target);
    setSelectedBranch(currentBranch ?? "");
  }, [currentBranch, open, target]);

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

  const branchChanged = selectedTarget === "local"
    && currentMode === "local"
    && Boolean(selectedBranch)
    && selectedBranch !== currentBranch;
  const canConfirm = selectedTarget !== currentMode || branchChanged;
  const worktreeTitle = associatedWorktreeId ? "已有受管工作树" : "新建受管工作树";
  const confirmLabel = busy
    ? "正在更改…"
    : !canConfirm
      ? "当前环境"
      : selectedTarget === "local"
        ? currentMode === "local"
          ? `切换到 ${selectedBranch}`
          : "切换到本地工作区"
        : associatedWorktreeId
          ? "返回工作树"
          : "创建并切换";

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel} aria-hidden={!open}>
      <div
        ref={dialogRef}
        className="modal-dialog modal-dialog--wide handoff-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="handoff-dialog-title"
        aria-describedby="handoff-dialog-description"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="handoff-dialog-title">更改执行环境</h3>
          <p className="modal-subtitle" id="handoff-dialog-description">
            当前会话、历史对话和检查点都会保留。后续任务会在所选环境中执行。
          </p>
        </div>
        <div className="modal-body handoff-dialog-body">
          <fieldset className="create-session-fieldset">
            <legend>执行方式</legend>
            <div className="create-session-mode-grid">
              <label className={`create-session-mode-card${selectedTarget === "local" ? " selected" : ""}`}>
                <input
                  type="radio"
                  name="handoff-target"
                  value="local"
                  checked={selectedTarget === "local"}
                  disabled={busy}
                  onChange={() => setSelectedTarget("local")}
                />
                <span>
                  <strong>本地工作区</strong>
                  <small>直接在项目目录和本地分支中执行</small>
                </span>
                {currentMode === "local" && <em>当前</em>}
              </label>
              <label className={`create-session-mode-card${selectedTarget === "worktree" ? " selected" : ""}`}>
                <input
                  type="radio"
                  name="handoff-target"
                  value="worktree"
                  checked={selectedTarget === "worktree"}
                  disabled={busy}
                  onChange={() => setSelectedTarget("worktree")}
                />
                <span>
                  <strong>{worktreeTitle}</strong>
                  <small>{associatedWorktreeId
                    ? "使用这个会话原有的独立工作树"
                    : "从当前本地分支创建独立工作树"}</small>
                </span>
                {currentMode === "worktree" && <em>当前</em>}
              </label>
            </div>
          </fieldset>
          <section className="handoff-selection-details" aria-live="polite">
            {selectedTarget === "local" && currentMode === "local" && branches.length > 0 && (
              <label className="create-session-ref-field" htmlFor="handoff-local-branch">
                <span>本地分支</span>
                <select
                  id="handoff-local-branch"
                  aria-label="本地分支"
                  value={selectedBranch}
                  disabled={busy}
                  onChange={(event) => setSelectedBranch(event.target.value)}
                >
                  {branches.map((branch) => <option value={branch} key={branch}>{branch}</option>)}
                </select>
              </label>
            )}
            {selectedTarget === "local" && currentMode === "worktree" && (
              <p>当前工作树的 Git 状态会安全同步到本地工作区</p>
            )}
            {selectedTarget === "worktree" && associatedWorktreeId && (
              <p>返回这个会话原有的独立工作树</p>
            )}
            {selectedTarget === "worktree" && !associatedWorktreeId && (
              <>
                <p>从本地分支 {currentBranch ?? "当前提交"} 创建独立工作树</p>
                {changedFileCount > 0 && <p>{changedFileCount} 个文件的当前修改会一起迁移</p>}
              </>
            )}
          </section>
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>
        <div className="modal-footer">
          <Button variant="ghost" disabled={busy} onClick={onCancel}>取消</Button>
          <Button
            ref={confirmRef}
            variant="primary"
            loading={busy}
            disabled={busy || !canConfirm}
            onClick={() => onConfirm(
              selectedTarget,
              selectedTarget === "local" && currentMode === "local" ? selectedBranch : undefined,
            )}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
