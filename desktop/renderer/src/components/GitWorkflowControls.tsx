import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  GitFetchResult,
  GitMergeResult,
  GitPullResult,
  GitPushResult,
  GitRemoteStatus,
  ProjectGitContext,
  SessionGitCommitResult,
  SessionGitMutationResult,
  SessionGitStatus,
} from "../contracts.js";
import { runtimeBusinessCode, userFacingError } from "../session-state.js";
import { Button } from "./Button.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";


interface GitWorkflowControlsProps {
  sessionId: string;
  workspaceRoot: string;
  status: SessionGitStatus;
  disabled: boolean;
  onRefresh(): void;
  openRequest?: number | undefined;
  onCreateBranch?: (() => void) | undefined;
  switchBranch?: (
    sessionId: string, branch: string, operationId: string,
  ) => Promise<SessionGitMutationResult>;
  readRemoteStatus?: (sessionId: string) => Promise<GitRemoteStatus>;
  readProjectGitContext?: (workspaceRoot: string) => Promise<ProjectGitContext>;
  commit?: (
    sessionId: string, message: string, operationId: string,
  ) => Promise<SessionGitCommitResult>;
  stage?: (sessionId: string, paths: string[], operationId: string) => Promise<unknown>;
  fetch?: (sessionId: string, operationId: string) => Promise<GitFetchResult>;
  pull?: (sessionId: string, operationId: string) => Promise<GitPullResult>;
  push?: (sessionId: string, operationId: string) => Promise<GitPushResult>;
  merge?: (
    sessionId: string, target: string, operationId: string,
  ) => Promise<GitMergeResult>;
  mergeAbort?: (sessionId: string, operationId: string) => Promise<GitMergeResult>;
  rebase?: (
    sessionId: string, target: string, operationId: string,
  ) => Promise<GitMergeResult>;
  rebaseContinue?: (sessionId: string, operationId: string) => Promise<GitMergeResult>;
  rebaseAbort?: (sessionId: string, operationId: string) => Promise<GitMergeResult>;
}

const defaults = {
  readRemoteStatus: (sessionId: string) => window.eidosRuntime.readSessionGitRemoteStatus(sessionId),
  readProjectGitContext: (workspaceRoot: string) => (
    window.eidosRuntime.readProjectGitContext(workspaceRoot)
  ),
  commit: (sessionId: string, message: string, operationId: string) => (
    window.eidosRuntime.commitSessionGit(sessionId, message, operationId)
  ),
  stage: (sessionId: string, paths: string[], operationId: string) => (
    window.eidosRuntime.stageSessionGit(sessionId, paths, operationId)
  ),
  switchBranch: (sessionId: string, branch: string, operationId: string) => (
    window.eidosRuntime.switchSessionGitBranch(sessionId, branch, operationId)
  ),
  fetch: (sessionId: string, operationId: string) => (
    window.eidosRuntime.fetchSessionGit(sessionId, operationId)
  ),
  pull: (sessionId: string, operationId: string) => (
    window.eidosRuntime.pullSessionGit(sessionId, operationId)
  ),
  push: (sessionId: string, operationId: string) => (
    window.eidosRuntime.pushSessionGit(sessionId, operationId)
  ),
  merge: (sessionId: string, target: string, operationId: string) => (
    window.eidosRuntime.mergeSessionGit(sessionId, target, operationId)
  ),
  mergeAbort: (sessionId: string, operationId: string) => (
    window.eidosRuntime.abortSessionGitMerge(sessionId, operationId)
  ),
  rebase: (sessionId: string, target: string, operationId: string) => (
    window.eidosRuntime.rebaseSessionGit(sessionId, target, operationId)
  ),
  rebaseContinue: (sessionId: string, operationId: string) => (
    window.eidosRuntime.continueSessionGitRebase(sessionId, operationId)
  ),
  rebaseAbort: (sessionId: string, operationId: string) => (
    window.eidosRuntime.abortSessionGitRebase(sessionId, operationId)
  ),
};

export function GitWorkflowControls({
  sessionId,
  workspaceRoot,
  status,
  disabled,
  onRefresh,
  openRequest,
  onCreateBranch,
  switchBranch = defaults.switchBranch,
  readRemoteStatus = defaults.readRemoteStatus,
  readProjectGitContext = defaults.readProjectGitContext,
  commit = defaults.commit,
  stage = defaults.stage,
  fetch = defaults.fetch,
  pull = defaults.pull,
  push = defaults.push,
  merge = defaults.merge,
  mergeAbort = defaults.mergeAbort,
  rebase = defaults.rebase,
  rebaseContinue = defaults.rebaseContinue,
  rebaseAbort = defaults.rebaseAbort,
}: GitWorkflowControlsProps) {
  const [remote, setRemote] = useState<GitRemoteStatus>();
  const [branches, setBranches] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [message, setMessage] = useState("");
  const [includeUnstaged, setIncludeUnstaged] = useState(true);
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [operation, setOperation] = useState<GitMergeResult>();
  const [workflowOpen, setWorkflowOpen] = useState(false);
  const operationIdsRef = useRef(new Map<string, string>());
  const dialogRef = useRef<HTMLDivElement>(null);
  const messageRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (openRequest !== undefined && openRequest > 0) setWorkflowOpen(true);
  }, [openRequest]);

  useDialogFocusLifecycle({ open: workflowOpen, initialFocusRef: messageRef });

  useEffect(() => {
    if (!workflowOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && busy === undefined) {
        event.preventDefault();
        setWorkflowOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, workflowOpen]);

  useEffect(() => {
    if (!workflowOpen) return;
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
  }, [workflowOpen]);

  const targets = useMemo(
    () => branches.filter((branch) => branch !== status.branch),
    [branches, status.branch],
  );

  const loadObservations = useCallback(async (): Promise<void> => {
    try {
      const [nextRemote, context] = await Promise.all([
        readRemoteStatus(sessionId),
        readProjectGitContext(workspaceRoot),
      ]);
      setRemote(nextRemote);
      setBranches(context.branches);
      setTarget((current) => (
        current && context.branches.includes(current) && current !== status.branch
          ? current
          : context.branches.find((branch) => branch !== status.branch) ?? ""
      ));
    } catch (cause: unknown) {
      setError(userFacingError(cause));
    }
  }, [readProjectGitContext, readRemoteStatus, sessionId, status.branch, workspaceRoot]);

  useEffect(() => {
    setRemote(undefined);
    setBranches([]);
    setOperation(undefined);
    setError(undefined);
    void loadObservations();
  }, [loadObservations]);

  const run = async <Result,>(
    name: string,
    requestKey: string,
    action: (operationId: string) => Promise<Result>,
    observe?: (result: Result) => void,
  ): Promise<Result | null> => {
    setBusy(name);
    setError(undefined);
    try {
      const operationId = operationIdsRef.current.get(requestKey) ?? crypto.randomUUID();
      operationIdsRef.current.set(requestKey, operationId);
      const result = await action(operationId);
      operationIdsRef.current.delete(requestKey);
      observe?.(result);
      onRefresh();
      await loadObservations();
      return result;
    } catch (cause: unknown) {
      const code = runtimeBusinessCode(cause);
      if (
        code !== undefined
        && code !== "OPERATION_IN_PROGRESS"
        && code !== "INTERNAL_ERROR"
      ) {
        operationIdsRef.current.delete(requestKey);
      }
      setError(userFacingError(cause));
      return null;
    } finally {
      setBusy(undefined);
    }
  };

  const runRemote = <Result extends GitRemoteStatus>(
    name: string,
    requestKey: string,
    action: (operationId: string) => Promise<Result>,
  ): Promise<Result | null> => run(name, requestKey, action, (result) => setRemote(result));

  const runOperation = (
    name: string,
    requestKey: string,
    action: (operationId: string) => Promise<GitMergeResult>,
  ): Promise<GitMergeResult | null> => run(name, requestKey, action, setOperation);

  const controlsDisabled = disabled || busy !== undefined;
  const upstream = remote?.upstream;
  const localSession = status.worktreeId === null;
  const canCreateBranch = onCreateBranch && (localSession || status.branch === null);
  const unstagedPaths = [...status.unstagedFiles, ...status.untrackedFiles];
  const canCommit = status.stagedCount > 0 || (includeUnstaged && unstagedPaths.length > 0);

  const commitChanges = async (pushAfterCommit: boolean): Promise<void> => {
    const commitMessage = message.trim();
    if (!commitMessage || status.branch === null) return;
    if (includeUnstaged && unstagedPaths.length > 0) {
      const staged = await run(
        "stage",
        `stage:${unstagedPaths.join("\u0000")}:${status.head}`,
        (operationId) => stage(sessionId, unstagedPaths, operationId),
      );
      if (staged === null) return;
    }
    const result = await run(
      "commit",
      `commit:${commitMessage}:${status.head}`,
      (operationId) => commit(sessionId, commitMessage, operationId),
    );
    if (!result) return;
    setMessage("");
    if (pushAfterCommit) {
      await runRemote(
        "push",
        `push:${status.branch}:${remote?.upstream?.remote ?? ""}:${remote?.upstream?.branch ?? ""}:${result.head}`,
        (operationId) => push(sessionId, operationId),
      );
    }
  };

  return (
    <section className="git-workflow-controls" aria-label="Git workflow">
      <div className="git-workflow-observation">
        {localSession && branches.length > 0 ? (
          <label className="git-local-branch-control">
            <span className="sr-only">当前本地分支</span>
            <select
              aria-label="当前本地分支"
              value={status.branch ?? ""}
              disabled={controlsDisabled || status.dirty}
              onChange={(event) => {
                const branch = event.target.value;
                if (branch && branch !== status.branch) {
                  void run(
                    "switch-branch",
                    `switch-branch:${branch}:${status.head}`,
                    (operationId) => switchBranch(sessionId, branch, operationId),
                  );
                }
              }}
            >
              {status.branch === null && <option value="">Detached HEAD</option>}
              {branches.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
            </select>
          </label>
        ) : (
          <strong>{status.branch ?? "Detached HEAD"}</strong>
        )}
        {upstream ? <span>{upstream.remote}/{upstream.branch}</span> : <span>No upstream</span>}
        {remote?.ahead !== null && remote?.ahead !== undefined
          && remote.behind !== null && remote.behind !== undefined && (
          <span>↑{remote.ahead} ↓{remote.behind}</span>
        )}
      </div>

      {canCreateBranch && (
        <Button
          size="small"
          variant={localSession ? "secondary" : "primary"}
          disabled={controlsDisabled || (localSession && status.dirty)}
          onClick={onCreateBranch}
        >
          {localSession ? "创建分支" : "在此创建分支"}
        </Button>
      )}

      <button
        type="button"
        className="git-workflow-trigger"
        aria-haspopup="dialog"
        aria-expanded={workflowOpen}
        onClick={() => setWorkflowOpen(true)}
      >
        <span>提交或推送</span>
      </button>
      {workflowOpen && (
        <div
          className="modal-backdrop git-workflow-modal-backdrop"
          onClick={busy === undefined ? () => setWorkflowOpen(false) : undefined}
        >
          <div
            ref={dialogRef}
            className="modal-dialog modal-dialog--wide git-workflow-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="git-workflow-dialog-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-header git-workflow-dialog-header">
              <div>
                <h3 id="git-workflow-dialog-title">提交和推送</h3>
                <p className="modal-subtitle">{status.branch ?? "Detached HEAD"}</p>
              </div>
              <button
                type="button"
                className="git-workflow-dialog-close"
                aria-label="关闭提交和推送"
                disabled={busy !== undefined}
                onClick={() => setWorkflowOpen(false)}
              >
                ×
              </button>
            </div>
            <div className="git-workflow-dialog-content">
          <form
            className="git-commit-form"
            onSubmit={(event) => {
              event.preventDefault();
              void commitChanges(false);
            }}
          >
            <input
              ref={messageRef}
              aria-label="提交信息"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="输入提交信息"
              maxLength={16_384}
            />
            <label className="git-include-unstaged">
              <input
                type="checkbox"
                checked={includeUnstaged}
                onChange={(event) => setIncludeUnstaged(event.target.checked)}
              />
              包含未暂存的更改
            </label>
            <div className="git-commit-actions">
              <Button type="submit" size="small" variant="primary"
                loading={busy === "commit"}
                disabled={controlsDisabled || status.branch === null || !canCommit || !message.trim()}>
                提交
              </Button>
              <Button type="button" size="small" variant="secondary"
                disabled={controlsDisabled || status.branch === null || !canCommit || !message.trim()}
                onClick={() => void commitChanges(true)}>
                提交并推送
              </Button>
            </div>
          </form>

          <div className="git-remote-actions">
            <Button size="small" disabled={controlsDisabled} loading={busy === "fetch"}
              onClick={() => void runRemote(
                "fetch", `fetch:${remote?.upstream?.remote ?? ""}:${status.head}`,
                (id) => fetch(sessionId, id),
              )}>获取</Button>
            <Button size="small" disabled={controlsDisabled || status.branch === null}
              loading={busy === "pull"}
              onClick={() => void runRemote(
                "pull", `pull:${remote?.upstream?.remote ?? ""}:${remote?.upstream?.branch ?? ""}:${status.head}`,
                (id) => pull(sessionId, id),
              )}>拉取</Button>
            <Button size="small" disabled={controlsDisabled || status.branch === null}
              loading={busy === "push"}
              onClick={() => void runRemote(
                "push", `push:${status.branch ?? ""}:${remote?.upstream?.remote ?? ""}:${remote?.upstream?.branch ?? ""}:${status.head}`,
                (id) => push(sessionId, id),
              )}>推送</Button>
          </div>

          <details className="git-advanced-controls">
            <summary>高级 Git</summary>
            <div>
              <select
                aria-label="Git target"
                value={target}
                disabled={controlsDisabled || status.branch === null || targets.length === 0}
                onChange={(event) => setTarget(event.target.value)}
              >
                {targets.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
              </select>
              <Button size="small" disabled={controlsDisabled || !target || status.branch === null}
                loading={busy === "merge"}
                onClick={() => void runOperation(
                  "merge", `merge:${target}:${status.head}`, (id) => merge(sessionId, target, id),
                )}>
                Merge
              </Button>
              <Button size="small" disabled={controlsDisabled || !target || status.branch === null}
                loading={busy === "rebase"}
                onClick={() => void runOperation(
                  "rebase", `rebase:${target}:${status.head}`, (id) => rebase(sessionId, target, id),
                )}>
                Rebase
              </Button>
            </div>
          </details>

          {operation?.operationState !== undefined && operation.operationState !== "none" && (
            <aside className="git-operation-conflict" aria-label="Git conflict operation">
              <strong>{operation.operationState === "merge" ? "Merge conflicts" : "Rebase conflicts"}</strong>
              <ul>{operation.conflictFiles.map((path) => <li key={path}>{path}</li>)}</ul>
              {operation.operationState === "merge" ? (
                <Button size="small" variant="danger" disabled={controlsDisabled}
                  onClick={() => void runOperation(
                    "merge-abort", "merge-abort", (id) => mergeAbort(sessionId, id),
                  )}>
                  Abort Merge
                </Button>
              ) : (
                <div>
                  <Button size="small" variant="primary" disabled={controlsDisabled}
                    onClick={() => void runOperation(
                      "rebase-continue", "rebase-continue", (id) => rebaseContinue(sessionId, id),
                    )}>
                    Continue Rebase
                  </Button>
                  <Button size="small" variant="danger" disabled={controlsDisabled}
                    onClick={() => void runOperation(
                      "rebase-abort", "rebase-abort", (id) => rebaseAbort(sessionId, id),
                    )}>
                    Abort Rebase
                  </Button>
                </div>
              )}
            </aside>
          )}
            </div>
          </div>
        </div>
      )}

      {error && <p className="approval-error git-workflow-error" role="alert">{error}</p>}
    </section>
  );
}
