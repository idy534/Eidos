import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  GitFetchResult,
  GitMergeResult,
  GitPullResult,
  GitPushResult,
  GitRemoteStatus,
  ProjectGitContext,
  SessionGitCommitResult,
  SessionGitStatus,
} from "../contracts.js";
import { userFacingError } from "../session-state.js";
import { Button } from "./Button.js";


interface GitWorkflowControlsProps {
  sessionId: string;
  workspaceRoot: string;
  status: SessionGitStatus;
  disabled: boolean;
  onRefresh(): void;
  onCreateBranch?: (() => void) | undefined;
  readRemoteStatus?: (sessionId: string) => Promise<GitRemoteStatus>;
  readProjectGitContext?: (workspaceRoot: string) => Promise<ProjectGitContext>;
  commit?: (
    sessionId: string, message: string, operationId: string,
  ) => Promise<SessionGitCommitResult>;
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
  onCreateBranch,
  readRemoteStatus = defaults.readRemoteStatus,
  readProjectGitContext = defaults.readProjectGitContext,
  commit = defaults.commit,
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
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const [operation, setOperation] = useState<GitMergeResult>();
  const operationIdsRef = useRef(new Map<string, string>());

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
  ): Promise<void> => {
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
    } catch (cause: unknown) {
      setError(userFacingError(cause));
    } finally {
      setBusy(undefined);
    }
  };

  const runRemote = <Result extends GitRemoteStatus>(
    name: string,
    action: (operationId: string) => Promise<Result>,
  ): Promise<void> => run(name, name, action, (result) => setRemote(result));

  const runOperation = (
    name: string,
    requestKey: string,
    action: (operationId: string) => Promise<GitMergeResult>,
  ): Promise<void> => run(name, requestKey, action, setOperation);

  const controlsDisabled = disabled || busy !== undefined;
  const upstream = remote?.upstream;

  return (
    <section className="git-workflow-controls" aria-label="Git workflow">
      <div className="git-workflow-observation">
        <strong>{status.branch ?? "Detached HEAD"}</strong>
        {upstream ? <span>{upstream.remote}/{upstream.branch}</span> : <span>No upstream</span>}
        {remote?.ahead !== null && remote?.ahead !== undefined
          && remote.behind !== null && remote.behind !== undefined && (
          <span>↑{remote.ahead} ↓{remote.behind}</span>
        )}
      </div>

      {status.branch === null && onCreateBranch && (
        <Button
          size="small"
          variant="primary"
          disabled={controlsDisabled}
          onClick={onCreateBranch}
        >
          Create Branch Here
        </Button>
      )}

      <form
        className="git-commit-form"
        onSubmit={(event) => {
          event.preventDefault();
          const commitMessage = message.trim();
          if (!commitMessage) return;
          void run("commit", `commit:${commitMessage}`, (operationId) => commit(
            sessionId, commitMessage, operationId,
          ), () => setMessage(""));
        }}
      >
        <input
          aria-label="Commit message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Commit message"
          maxLength={16_384}
        />
        <Button
          type="submit"
          size="small"
          variant="primary"
          loading={busy === "commit"}
          disabled={controlsDisabled || status.branch === null || status.stagedCount === 0 || !message.trim()}
        >
          Commit
        </Button>
      </form>

      <div className="git-remote-actions">
        <Button size="small" disabled={controlsDisabled} loading={busy === "fetch"}
          onClick={() => void runRemote("fetch", (id) => fetch(sessionId, id))}>Fetch</Button>
        <Button size="small" disabled={controlsDisabled || status.branch === null}
          loading={busy === "pull"}
          onClick={() => void runRemote("pull", (id) => pull(sessionId, id))}>Pull</Button>
        <Button size="small" disabled={controlsDisabled || status.branch === null}
          loading={busy === "push"}
          onClick={() => void runRemote("push", (id) => push(sessionId, id))}>Push</Button>
      </div>

      <details className="git-advanced-controls">
        <summary>Advanced Git</summary>
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
              "merge", `merge:${target}`, (id) => merge(sessionId, target, id),
            )}>
            Merge
          </Button>
          <Button size="small" disabled={controlsDisabled || !target || status.branch === null}
            loading={busy === "rebase"}
            onClick={() => void runOperation(
              "rebase", `rebase:${target}`, (id) => rebase(sessionId, target, id),
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

      {error && <p className="approval-error git-workflow-error" role="alert">{error}</p>}
    </section>
  );
}
