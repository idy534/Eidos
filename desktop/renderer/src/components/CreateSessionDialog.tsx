import { useEffect, useRef, useState } from "react";

import type { ProjectGitContext } from "../contracts.js";
import { Button } from "./Button.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

type ExecutionMode = "local" | "worktree";

interface CreateSessionDialogProps {
  open: boolean;
  workspaceRoot: string;
  gitContext: ProjectGitContext;
  busy?: boolean;
  error?: string | undefined;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
  onConfirm: (
    executionMode: ExecutionMode,
    baseRef?: string,
    includeLocalChanges?: boolean,
  ) => void;
  onCancel: () => void;
}

export function CreateSessionDialog({
  open,
  workspaceRoot,
  gitContext,
  busy = false,
  error,
  getFallbackFocus,
  onConfirm,
  onCancel,
}: CreateSessionDialogProps) {
  const [executionMode, setExecutionMode] = useState<ExecutionMode>(
    gitContext.gitAvailable ? "worktree" : "local",
  );
  const [baseRef, setBaseRef] = useState(gitContext.currentBranch ?? "HEAD");
  const [includeLocalChanges, setIncludeLocalChanges] = useState(
    Boolean(gitContext.dirty && gitContext.currentBranch !== null),
  );
  const cancelButtonRef = useRef<HTMLButtonElement>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setExecutionMode(gitContext.gitAvailable ? "worktree" : "local");
    setBaseRef(gitContext.currentBranch ?? "HEAD");
    setIncludeLocalChanges(
      Boolean(gitContext.dirty && gitContext.currentBranch !== null),
    );
  }, [open, gitContext.gitAvailable, gitContext.currentBranch]);

  useDialogFocusLifecycle({
    open,
    initialFocusRef: confirmButtonRef,
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
          "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])",
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

  const branchOptions = [
    ...(gitContext.currentBranch && !gitContext.branches.includes(gitContext.currentBranch)
      ? [gitContext.currentBranch]
      : []),
    ...gitContext.branches.filter((branch) => branch !== gitContext.currentBranch),
  ];
  const startingRefs = baseRef === "HEAD" && !branchOptions.includes("HEAD")
    ? ["HEAD", ...branchOptions]
    : branchOptions;

  return (
    <div
      className="modal-backdrop"
      onClick={busy ? undefined : onCancel}
      aria-hidden={!open}
    >
      <div
        ref={dialogRef}
        className="modal-dialog modal-dialog--wide create-session-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-session-dialog-title"
        aria-describedby="create-session-dialog-description"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="create-session-dialog-title">新建任务</h3>
          <p className="modal-subtitle" id="create-session-dialog-description">
            {workspaceRoot}
          </p>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onConfirm(
              executionMode,
              executionMode === "worktree" ? baseRef || "HEAD" : undefined,
              executionMode === "worktree" ? includeLocalChanges : false,
            );
          }}
        >
          <div className="modal-body create-session-dialog-body">
            <fieldset className="create-session-fieldset">
              <legend>执行方式</legend>
              <div className="create-session-mode-grid">
                <label className={`create-session-mode-card${executionMode === "local" ? " selected" : ""}`}>
                  <input
                    type="radio"
                    name="execution-mode"
                    value="local"
                    checked={executionMode === "local"}
                    onChange={() => setExecutionMode("local")}
                  />
                  <span>
                    <strong>本地</strong>
                    <small>直接在项目目录中执行</small>
                  </span>
                </label>
                <label
                  className={`create-session-mode-card${executionMode === "worktree" ? " selected" : ""}${!gitContext.gitAvailable ? " disabled" : ""}`}
                >
                  <input
                    type="radio"
                    name="execution-mode"
                    value="worktree"
                    checked={executionMode === "worktree"}
                    disabled={!gitContext.gitAvailable}
                    onChange={() => setExecutionMode("worktree")}
                  />
                  <span>
                    <strong>新建本地工作树</strong>
                    <small>{gitContext.gitAvailable ? "创建分离状态的独立工作树" : "需要 Git 项目"}</small>
                  </span>
                </label>
              </div>
            </fieldset>

            {executionMode === "worktree" && gitContext.gitAvailable && (
              <label className="create-session-ref-field" htmlFor="create-session-base-ref">
                <span>起始分支</span>
                <select
                  id="create-session-base-ref"
                  value={baseRef}
                  onChange={(event) => {
                    const nextBaseRef = event.target.value;
                    setBaseRef(nextBaseRef);
                    setIncludeLocalChanges(
                      Boolean(gitContext.dirty && nextBaseRef === gitContext.currentBranch),
                    );
                  }}
                >
                  {startingRefs.map((ref) => <option value={ref} key={ref}>{ref}</option>)}
                </select>
              </label>
            )}
            {executionMode === "worktree" && gitContext.gitAvailable && gitContext.dirty && (
              <label className="create-session-changes-option" htmlFor="include-local-changes">
                <input
                  id="include-local-changes"
                  type="checkbox"
                  checked={includeLocalChanges}
                  onChange={(event) => setIncludeLocalChanges(event.target.checked)}
                />
                <span>
                  <strong>包含当前修改</strong>
                  <small>
                    {gitContext.changedFileCount > 0
                      ? `${gitContext.changedFileCount} 个文件有未提交修改`
                      : "复制当前工作区修改"}
                  </small>
                </span>
              </label>
            )}
            {error && <p className="setting-field-error" role="alert">{error}</p>}
          </div>
          <div className="modal-footer">
            <Button
              ref={cancelButtonRef}
              variant="ghost"
              disabled={busy}
              onClick={onCancel}
            >
              取消
            </Button>
            <Button
              ref={confirmButtonRef}
              type="submit"
              variant="primary"
              loading={busy}
            >
              创建任务
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
