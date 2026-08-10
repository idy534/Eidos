import { useCallback, useEffect, useRef, useState } from "react";

import type {
  GitDiffScope,
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
  statusBySessionId: ReadonlyMap<string, SessionGitStatus>;
  diff: SessionGitDiff | undefined;
  loadingStatus: boolean;
  loadingDiff: boolean;
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

export function useGitReviewController({
  ready,
  session,
}: GitReviewControllerOptions): readonly [GitReviewState, GitReviewActions] {
  const [scope, setScope] = useState<GitDiffScope>("head");
  const [status, setStatus] = useState<SessionGitStatus | undefined>(undefined);
  const [statusBySessionId, setStatusBySessionId] = useState<ReadonlyMap<string, SessionGitStatus>>(
    () => new Map(),
  );
  const [diff, setDiff] = useState<SessionGitDiff | undefined>(undefined);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);

  const generationRef = useRef(0);
  const statusRequestRef = useRef(0);
  const diffRequestRef = useRef(0);
  const selectedSessionIdRef = useRef<string | undefined>(undefined);
  const managedRef = useRef(false);
  const readyRef = useRef(ready);
  const scopeRef = useRef<GitDiffScope>(scope);
  const refreshTimerRef = useRef<number | undefined>(undefined);

  readyRef.current = ready;
  selectedSessionIdRef.current = session?.id;
  managedRef.current = session?.worktree !== undefined;

  const loadStatus = useCallback((sessionId: string, generation: number): void => {
    const request = statusRequestRef.current + 1;
    statusRequestRef.current = request;
    setLoadingStatus(true);
    setError(undefined);
    void window.eidosRuntime.readSessionGitStatus(sessionId).then((nextStatus) => {
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

  const loadDiff = useCallback((
    sessionId: string,
    nextScope: GitDiffScope,
    generation: number,
  ): void => {
    const request = diffRequestRef.current + 1;
    diffRequestRef.current = request;
    setLoadingDiff(true);
    setError(undefined);
    void window.eidosRuntime.readSessionGitDiff(sessionId, nextScope).then((nextDiff) => {
      if (
        generationRef.current !== generation
        || diffRequestRef.current !== request
        || selectedSessionIdRef.current !== sessionId
        || scopeRef.current !== nextScope
      ) return;
      setDiff(nextDiff);
    }).catch((cause: unknown) => {
      if (
        generationRef.current === generation
        && diffRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
        && scopeRef.current === nextScope
      ) setError(userFacingError(cause));
    }).finally(() => {
      if (
        generationRef.current === generation
        && diffRequestRef.current === request
        && selectedSessionIdRef.current === sessionId
        && scopeRef.current === nextScope
      ) setLoadingDiff(false);
    });
  }, []);

  const refresh = useCallback((): void => {
    const sessionId = selectedSessionIdRef.current;
    if (!readyRef.current || !managedRef.current || !sessionId) return;
    const generation = generationRef.current;
    loadStatus(sessionId, generation);
    loadDiff(sessionId, scopeRef.current, generation);
  }, [loadDiff, loadStatus]);

  const selectScope = useCallback((nextScope: GitDiffScope): void => {
    scopeRef.current = nextScope;
    setScope(nextScope);
    setDiff(undefined);
    const sessionId = selectedSessionIdRef.current;
    if (!readyRef.current || !managedRef.current || !sessionId) return;
    loadDiff(sessionId, nextScope, generationRef.current);
  }, [loadDiff]);

  const handleNotification = useCallback((notification: RuntimeNotification): void => {
    const sessionId = selectedSessionIdRef.current;
    if (
      !readyRef.current
      || !managedRef.current
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
    const generation = generationRef.current;
    scopeRef.current = "head";
    setScope("head");
    setStatus(undefined);
    setDiff(undefined);
    setLoadingStatus(false);
    setLoadingDiff(false);
    setError(undefined);
    if (refreshTimerRef.current !== undefined) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = undefined;
    }
    if (!ready || !session?.worktree) return;
    loadStatus(session.id, generation);
    loadDiff(session.id, "head", generation);
  }, [loadDiff, loadStatus, ready, session?.id, session?.worktree?.worktreeId]);

  useEffect(() => () => {
    generationRef.current += 1;
    if (refreshTimerRef.current !== undefined) {
      window.clearTimeout(refreshTimerRef.current);
    }
  }, []);

  return [{
    scope,
    status,
    statusBySessionId,
    diff,
    loadingStatus,
    loadingDiff,
    error,
  }, {
    selectScope,
    refresh,
    handleNotification,
  }] as const;
}
