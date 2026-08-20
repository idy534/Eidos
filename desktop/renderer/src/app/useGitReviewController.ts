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

  const loadProjectContext = useCallback((
    sessionId: string,
    generation: number,
    workspaceRoot: string,
  ): void => {
    const request = contextRequestRef.current + 1;
    contextRequestRef.current = request;
    void window.eidosRuntime.readProjectGitContext(workspaceRoot).then((context) => {
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
  ): void => {
    const request = summaryRequestRef.current + 1;
    summaryRequestRef.current = request;
    setLoadingSummary(true);
    void window.eidosRuntime.readSessionGitDiff(sessionId, nextScope).then((nextSummary) => {
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

  const refresh = useCallback((): void => {
    const sessionId = selectedSessionIdRef.current;
    if (!readyRef.current || !gitAvailableRef.current || !sessionId) return;
    const generation = generationRef.current;
    loadStatus(sessionId, generation);
    loadSummary(sessionId, generation, scopeRef.current);
    const localSession = session?.executionMode === "local"
      || (session?.executionMode === undefined && session?.worktree === undefined);
    if (localSession && session?.project?.workspaceRoot) {
      loadProjectContext(sessionId, generation, session.project.workspaceRoot);
    }
  }, [
    loadProjectContext,
    loadSummary,
    loadStatus,
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
    const generation = generationRef.current;
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
    loadStatus(session.id, generation);
    loadSummary(session.id, generation, "baseline");
    const localSession = session.executionMode === "local"
      || (session.executionMode === undefined && session.worktree === undefined);
    if (localSession && session.project.workspaceRoot) {
      loadProjectContext(session.id, generation, session.project.workspaceRoot);
    }
  }, [
    loadProjectContext,
    loadSummary,
    loadStatus,
    ready,
    session?.id,
    session?.project?.gitAvailable,
    session?.executionMode,
    session?.associatedWorktreeId,
    session?.worktree?.worktreeId,
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
