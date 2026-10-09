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
  const worktreeTitle = associatedWorktreeId ? "已有本地工作树" : "新建本地工作树";
  const confirmLabel = busy
    ? "正在更改…"
    : !canConfirm
      ? "当前环境"
      : selectedTarget === "local"
        ? currentMode === "local"
          ? `切换到 ${selectedBranch}`
          : "切换到本地"
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
        <div className="modal-header handoff-modal-header">
          <div className="handoff-header-icon" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M7 16V4M7 4L3 8M7 4L11 8" />
              <path d="M17 8V20M17 20L21 16M17 20L13 16" />
            </svg>
          </div>
          <div className="handoff-header-text">
            <h3 id="handoff-dialog-title">更改工作环境</h3>
            <p className="modal-subtitle" id="handoff-dialog-description">
              当前会话、历史对话和检查点都会保留。后续任务会在所选工作环境中执行。
            </p>
          </div>
        </div>
        <div className="modal-body handoff-dialog-body">
          <fieldset className="create-session-fieldset handoff-fieldset">
            <legend className="handoff-legend">执行方式</legend>
            <div className="create-session-mode-grid handoff-mode-grid">
              <label
                className={`create-session-mode-card handoff-mode-card${selectedTarget === "local" ? " selected is-selected" : ""}${busy ? " is-disabled" : ""}`}
              >
                <input
                  type="radio"
                  name="handoff-target"
                  value="local"
                  checked={selectedTarget === "local"}
                  disabled={busy}
                  onChange={() => setSelectedTarget("local")}
                  className="handoff-radio-input"
                  aria-label="本地"
                />
                <span className="handoff-radio-indicator" aria-hidden="true">
                  <span className="handoff-radio-dot" />
                </span>
                <div className="handoff-card-content">
                  <div className="handoff-card-header">
                    <span className="handoff-card-icon" aria-hidden="true">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="2" y="3" width="20" height="14" rx="2" />
                        <path d="M8 21h8" />
                        <path d="M12 17v4" />
                      </svg>
                    </span>
                    <strong className="handoff-card-title">本地</strong>
                    {currentMode === "local" && (
                      <span className="handoff-current-badge">
                        <em>当前</em>
                      </span>
                    )}
                  </div>
                  <small className="handoff-card-desc">直接在项目目录和本地分支中执行</small>
                </div>
              </label>
              <label
                className={`create-session-mode-card handoff-mode-card${selectedTarget === "worktree" ? " selected" : ""}${busy ? " is-disabled" : ""}`}
              >
                <input
                  type="radio"
                  name="handoff-target"
                  value="worktree"
                  checked={selectedTarget === "worktree"}
                  disabled={busy}
                  onChange={() => setSelectedTarget("worktree")}
                  className="handoff-radio-input"
                  aria-label={worktreeTitle}
                />
                <span className="handoff-radio-indicator" aria-hidden="true">
                  <span className="handoff-radio-dot" />
                </span>
                <div className="handoff-card-content">
                  <div className="handoff-card-header">
                    <span className="handoff-card-icon" aria-hidden="true">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="6" y1="3" x2="6" y2="15" />
                        <circle cx="18" cy="6" r="3" />
                        <circle cx="6" cy="18" r="3" />
                        <path d="M18 9a9 9 0 0 1-9 9" />
                      </svg>
                    </span>
                    <strong className="handoff-card-title">{worktreeTitle}</strong>
                    {currentMode === "worktree" && (
                      <span className="handoff-current-badge">
                        <em>当前</em>
                      </span>
                    )}
                  </div>
                  <small className="handoff-card-desc">
                    {associatedWorktreeId
                      ? "使用这个会话原有的独立工作树"
                      : "从当前本地分支创建独立工作树"}
                  </small>
                </div>
              </label>
            </div>
          </fieldset>
          <section className="handoff-selection-details" aria-live="polite">
            {selectedTarget === "local" && currentMode === "local" && branches.length > 0 ? (
              <div className="handoff-branch-row">
                <span className="handoff-branch-icon" aria-hidden="true">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="6" y1="3" x2="6" y2="15" />
                    <circle cx="18" cy="6" r="3" />
                    <circle cx="6" cy="18" r="3" />
                    <path d="M18 9a9 9 0 0 1-9 9" />
                  </svg>
                </span>
                <label className="handoff-branch-label" htmlFor="handoff-local-branch">
                  本地分支
                </label>
                <div className="handoff-branch-select-wrap">
                  <select
                    id="handoff-local-branch"
                    aria-label="本地分支"
                    value={selectedBranch}
                    disabled={busy}
                    onChange={(event) => setSelectedBranch(event.target.value)}
                  >
                    {branches.map((branch) => <option value={branch} key={branch}>{branch}</option>)}
                  </select>
                  <span className="handoff-select-chevron" aria-hidden="true">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </span>
                </div>
              </div>
            ) : (
              <div className="handoff-detail-info-row">
                <div className="handoff-details-icon" aria-hidden="true">
                  {selectedTarget === "local" && currentMode === "local" ? (
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="6" y1="3" x2="6" y2="15" />
                      <circle cx="18" cy="6" r="3" />
                      <circle cx="6" cy="18" r="3" />
                      <path d="M18 9a9 9 0 0 1-9 9" />
                    </svg>
                  ) : selectedTarget === "local" && currentMode === "worktree" ? (
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                      <path d="M3 3v5h5" />
                      <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
                      <path d="M16 21h5v-5" />
                    </svg>
                  ) : (
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polygon points="12 2 2 7 12 12 22 7 12 2" />
                      <polyline points="2 17 12 22 22 17" />
                      <polyline points="2 12 12 17 22 12" />
                    </svg>
                  )}
                </div>
                <div className="handoff-details-content">
                  {selectedTarget === "local" && currentMode === "local" && branches.length === 0 && (
                    <p>当前已处于本地环境</p>
                  )}
                  {selectedTarget === "local" && currentMode === "worktree" && (
                    <p>当前工作树的 Git 状态会安全同步到本地</p>
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
                </div>
              </div>
            )}
          </section>
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>
        <div className="modal-footer handoff-modal-footer">
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
