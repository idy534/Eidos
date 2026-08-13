import { useCallback, useEffect, useRef, useState } from "react";

import type {
  GitDiffScope,
  RuntimeNotification,
  Session,
  SessionGitStatus,
} from "../contracts.js";
import { userFacingError } from "../session-state.js";


const COMPLETION_REFRESH_DELAY_MS = 120;

export interface GitReviewState {
  scope: GitDiffScope;
  status: SessionGitStatus | undefined;
  statusBySessionId: ReadonlyMap<string, SessionGitStatus>;
  loadingStatus: boolean;
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
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);

  const generationRef = useRef(0);
  const statusRequestRef = useRef(0);
  const selectedSessionIdRef = useRef<string | undefined>(undefined);
  const gitAvailableRef = useRef(false);
  const readyRef = useRef(ready);
  const scopeRef = useRef<GitDiffScope>(scope);
  const refreshTimerRef = useRef<number | undefined>(undefined);

  readyRef.current = ready;
  selectedSessionIdRef.current = session?.id;
  gitAvailableRef.current = session?.project?.gitAvailable === true;

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

  const refresh = useCallback((): void => {
    const sessionId = selectedSessionIdRef.current;
    if (!readyRef.current || !gitAvailableRef.current || !sessionId) return;
    const generation = generationRef.current;
    loadStatus(sessionId, generation);
  }, [loadStatus]);

  const selectScope = useCallback((nextScope: GitDiffScope): void => {
    scopeRef.current = nextScope;
    setScope(nextScope);
  }, []);

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
    const generation = generationRef.current;
    scopeRef.current = "head";
    setScope("head");
    setStatus(undefined);
    setLoadingStatus(false);
    setError(undefined);
    if (refreshTimerRef.current !== undefined) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = undefined;
    }
    if (!ready || session?.project?.gitAvailable !== true) return;
    loadStatus(session.id, generation);
  }, [
    loadStatus,
    ready,
    session?.id,
    session?.project?.gitAvailable,
    session?.executionMode,
    session?.associatedWorktreeId,
  ]);

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
    loadingStatus,
    error,
  }, {
    selectScope,
    refresh,
    handleNotification,
  }] as const;
}
