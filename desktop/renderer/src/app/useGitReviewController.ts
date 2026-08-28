import { useCallback, useEffect, useRef, useState } from "react";

import type {
  GitDiffScope,
  ProjectGitContext,
  RuntimeNotification,
  Session,
  SessionGitDiff,
  SessionGitStatus,
} from "../contracts.js";
import { userFacingError } from "../session-state.js";


const COMPLETION_REFRESH_DELAY_MS = 120;

export interface GitReviewState {
  scope: GitDiffScope;
  status: SessionGitStatus | undefined;
  summary: SessionGitDiff | undefined;
  projectContext: ProjectGitContext | undefined;
  statusBySessionId: ReadonlyMap<string, SessionGitStatus>;
  loadingStatus: boolean;
  loadingSummary: boolean;
  error: string | undefined;
}

export interface GitReviewActions {
  selectScope(scope: GitDiffScope): void;
  refresh(): void;
  handleNotification(notification: RuntimeNotification): void;
}

interface GitReviewControllerOptions {
  ready: boolean;
  session: Session | undefined;
}

interface GitRefreshRequest {
  sessionId: string;
  generation: number;
  epoch: number;
  scope: GitDiffScope;
  workspaceRoot: string | undefined;
}

export function useGitReviewController({
  ready,
  session,
}: GitReviewControllerOptions): readonly [GitReviewState, GitReviewActions] {
  const [scope, setScope] = useState<GitDiffScope>("baseline");
  const [status, setStatus] = useState<SessionGitStatus | undefined>(undefined);
  const [summary, setSummary] = useState<SessionGitDiff | undefined>(undefined);
  const [projectContext, setProjectContext] = useState<ProjectGitContext | undefined>(undefined);
  const [statusBySessionId, setStatusBySessionId] = useState<ReadonlyMap<string, SessionGitStatus>>(
    () => new Map(),
  );
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);

  const generationRef = useRef(0);
  const statusRequestRef = useRef(0);
  const summaryRequestRef = useRef(0);
  const contextRequestRef = useRef(0);
  const selectedSessionIdRef = useRef<string | undefined>(undefined);
  const gitAvailableRef = useRef(false);
  const readyRef = useRef(ready);
  const scopeRef = useRef<GitDiffScope>(scope);
  const refreshTimerRef = useRef<number | undefined>(undefined);
  const refreshEpochRef = useRef(0);
  const refreshRequestRef = useRef<GitRefreshRequest | undefined>(undefined);
  const refreshPromiseRef = useRef<Promise<void> | undefined>(undefined);

  readyRef.current = ready;
  selectedSessionIdRef.current = session?.id;
  gitAvailableRef.current = session?.project?.gitAvailable === true;

  const loadStatus = useCallback((sessionId: string, generation: number): Promise<void> => {
    const request = statusRequestRef.current + 1;
    statusRequestRef.current = request;
    setLoadingStatus(true);
    setError(undefined);
    return window.eidosRuntime.readSessionGitStatus(sessionId).then((nextStatus) => {
      if (
        generationRef.current !== generation
        || statusRequestRef.current !== request
        || selectedSessionIdRef.current !== sessionId
      ) return;
      setStatus(nextStatus);
      setStatusBySessionId((previous) => {
        const next = new Map(previous);
        next.set(sessionId, nextStatus);
        return next;
      });
    }).catch((cause: unknown) => {
      if (
        generationRef.current === generation
        && statusRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
      ) setError(userFacingError(cause));
    }).finally(() => {
      if (
        generationRef.current === generation
        && statusRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
      ) setLoadingStatus(false);
    });
  }, []);

  const loadProjectContext = useCallback((
    sessionId: string,
    generation: number,
    workspaceRoot: string,
  ): Promise<void> => {
    const request = contextRequestRef.current + 1;
    contextRequestRef.current = request;
    return window.eidosRuntime.readProjectGitContext(workspaceRoot).then((context) => {
      if (
        generationRef.current === generation
        && contextRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
      ) setProjectContext(context);
    }).catch((cause: unknown) => {
      if (
        generationRef.current === generation
        && contextRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
      ) setError(userFacingError(cause));
    });
  }, []);

  const loadSummary = useCallback((
    sessionId: string,
    generation: number,
    nextScope: GitDiffScope,
  ): Promise<void> => {
    const request = summaryRequestRef.current + 1;
    summaryRequestRef.current = request;
    setLoadingSummary(true);
    return window.eidosRuntime.readSessionGitDiff(sessionId, nextScope).then((nextSummary) => {
      if (
        generationRef.current === generation
        && summaryRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
        && scopeRef.current === nextScope
      ) setSummary(nextSummary);
    }).catch((cause: unknown) => {
      if (
        generationRef.current === generation
        && summaryRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
      ) setError(userFacingError(cause));
    }).finally(() => {
      if (
        generationRef.current === generation
        && summaryRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
      ) setLoadingSummary(false);
    });
  }, []);

  const runRefresh = useCallback(async (request: GitRefreshRequest): Promise<void> => {
    if (
      generationRef.current !== request.generation
      || selectedSessionIdRef.current !== request.sessionId
    ) return;
    const operations: Promise<void>[] = [
      loadStatus(request.sessionId, request.generation),
      loadSummary(request.sessionId, request.generation, request.scope),
    ];
    if (request.workspaceRoot !== undefined) {
      operations.push(
        loadProjectContext(request.sessionId, request.generation, request.workspaceRoot),
      );
    }
    await Promise.all(operations);
  }, [loadProjectContext, loadStatus, loadSummary]);

  const refresh = useCallback((): void => {
    const sessionId = selectedSessionIdRef.current;
    if (!readyRef.current || !gitAvailableRef.current || !sessionId) return;
    const generation = generationRef.current;
    const localSession = session?.executionMode === "local"
      || (session?.executionMode === undefined && session?.worktree === undefined);
    refreshRequestRef.current = {
      sessionId,
      generation,
      epoch: refreshEpochRef.current,
      scope: scopeRef.current,
      workspaceRoot: localSession ? session?.project?.workspaceRoot : undefined,
    };
    if (refreshPromiseRef.current) return;

    const epoch = refreshEpochRef.current;
    const refreshPromise = (async (): Promise<void> => {
      while (refreshRequestRef.current?.epoch === epoch) {
        const request = refreshRequestRef.current;
        if (!request) return;
        refreshRequestRef.current = undefined;
        await runRefresh(request);
      }
    })();
    refreshPromiseRef.current = refreshPromise;
    const settleRefresh = (): void => {
      if (refreshPromiseRef.current === refreshPromise) {
        refreshPromiseRef.current = undefined;
      }
      if (refreshRequestRef.current?.epoch === epoch) refresh();
    };
    void refreshPromise.then(settleRefresh, settleRefresh);
  }, [
    runRefresh,
    session?.executionMode,
    session?.project?.workspaceRoot,
    session?.worktree?.worktreeId,
  ]);

  const selectScope = useCallback((nextScope: GitDiffScope): void => {
    scopeRef.current = nextScope;
    setScope(nextScope);
    const sessionId = selectedSessionIdRef.current;
    if (readyRef.current && gitAvailableRef.current && sessionId) {
      setSummary(undefined);
      loadSummary(sessionId, generationRef.current, nextScope);
    }
  }, [loadSummary]);

  const handleNotification = useCallback((notification: RuntimeNotification): void => {
    const sessionId = selectedSessionIdRef.current;
    if (
      !readyRef.current
      || !gitAvailableRef.current
      || !sessionId
      || notification.params.sessionId !== sessionId
    ) return;
    const shouldRefresh = notification.method === "run/completed" || (
      notification.method === "item/completed"
      && ["file_change", "command_execution"].includes(notification.params.item.kind)
    );
    if (!shouldRefresh) return;
    if (refreshTimerRef.current !== undefined) {
      window.clearTimeout(refreshTimerRef.current);
    }
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = undefined;
      refresh();
    }, COMPLETION_REFRESH_DELAY_MS);
  }, [refresh]);

  useEffect(() => {
    generationRef.current += 1;
    refreshEpochRef.current += 1;
    refreshRequestRef.current = undefined;
    refreshPromiseRef.current = undefined;
    scopeRef.current = "baseline";
    setScope("baseline");
    setStatus(undefined);
    setSummary(undefined);
    setProjectContext(undefined);
    setLoadingStatus(false);
    setLoadingSummary(false);
    setError(undefined);
    if (refreshTimerRef.current !== undefined) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = undefined;
    }
    if (!ready || session?.project?.gitAvailable !== true) return;
    refresh();
  }, [
    ready,
    refresh,
    session?.id,
    session?.project?.gitAvailable,
    session?.executionMode,
    session?.associatedWorktreeId,
    session?.worktree?.worktreeId,
  ]);

  useEffect(() => () => {
    generationRef.current += 1;
    refreshRequestRef.current = undefined;
    if (refreshTimerRef.current !== undefined) {
      window.clearTimeout(refreshTimerRef.current);
    }
  }, []);

  return [{
    scope,
    status,
    summary,
    projectContext,
    statusBySessionId,
    loadingStatus,
    loadingSummary,
    error,
  }, {
    selectScope,
    refresh,
    handleNotification,
  }] as const;
}
