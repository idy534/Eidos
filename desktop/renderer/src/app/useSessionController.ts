import { useCallback, useRef, useState } from "react";
import type { Run, Session, SessionSnapshot } from "../contracts.js";
import { SnapshotReadCoordinator, taskStatusFromRun, upsertRun, userFacingError } from "../session-state.js";

const READ_COMPLETIONS_KEY = "eidos.readCompletedSessionIds";

function loadReadCompletedSessions(): Set<string> {
  try {
    const stored = JSON.parse(window.localStorage.getItem(READ_COMPLETIONS_KEY) ?? "[]");
    return new Set(
      Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : [],
    );
  } catch {
    return new Set();
  }
}

function saveReadCompletedSessions(set: Set<string>): void {
  try {
    window.localStorage.setItem(READ_COMPLETIONS_KEY, JSON.stringify([...set]));
  } catch {
    // localStorage failure is non-critical
  }
}

export interface PendingOperations {
  creatingSession?: boolean;
  selectingSessionId?: string;
  renamingSessionId?: string;
  deletingSessionId?: string;
}

export interface SessionControllerState {
  sessions: Session[];
  snapshot: SessionSnapshot | undefined;
  navigationSessionId: string | undefined;
  readCompletedSessions: ReadonlySet<string>;
  pending: PendingOperations;
  error: string | undefined;
}

export interface SessionControllerActions {
  loadSessions: () => Promise<void>;
  selectSession: (session: Session) => Promise<SessionSnapshot | undefined>;
  createSession: (workspaceRoot?: string) => Promise<SessionSnapshot | undefined>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  deleteSession: (session: Session) => Promise<{ confirmed: true } | { confirmed: false; error: string }>;
  setError: (error: string | undefined) => void;
  projectRun: (sessionId: string, run: Run) => void;
  /** Called when a session title notification arrives — updates sessions title and open snapshot */
  handleTitleNotification: (params: { sessionId: string; title: string }) => void;
  /** Called when a run notification arrives — updates sessions taskStatus */
  handleRunNotification: (run: { id: string; sessionId: string; status: string; updatedAt: number }) => void;
  /** Called when a session completes — triggers authoritative snapshot refresh */
  refreshCompletedSession: (sessionId: string) => Promise<void>;
  setSnapshot: (updater: (prev: SessionSnapshot | undefined) => SessionSnapshot | undefined) => void;
  setSessions: (sessions: Session[]) => void;
}

interface SessionSelectionOperation {
  token: symbol;
  sessionId: string;
  promise: Promise<SessionSnapshot | undefined>;
}

export function useSessionController(): [SessionControllerState, SessionControllerActions] {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | undefined>(undefined);
  const [navigationSessionId, setNavigationSessionId] = useState<string | undefined>(undefined);
  const [readCompletedSessions, setReadCompletedSessions] = useState<Set<string>>(loadReadCompletedSessions);
  const [pending, setPending] = useState<PendingOperations>({});
  const [error, setError] = useState<string | undefined>(undefined);

  /** Remove a key from pending state (compatible with exactOptionalPropertyTypes). */
  function clearPending<K extends keyof PendingOperations>(key: K): void {
    setPending((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  // Refs for concurrency safety
  const snapshotReads = useRef(new SnapshotReadCoordinator()).current;
  const selectedSessionIdRef = useRef<string | undefined>(undefined);
  const creatingSessionRef = useRef<boolean>(false);
  const activeSelectionRef = useRef<SessionSelectionOperation | undefined>(undefined);

  function updateReadCompleted(updater: (prev: Set<string>) => Set<string>): void {
    setReadCompletedSessions((prev) => {
      const next = updater(prev);
      saveReadCompletedSessions(next);
      return next;
    });
  }

  async function loadAuthoritativeSnapshot(sessionId: string): Promise<SessionSnapshot> {
    let loaded = await window.eidosRuntime.readSession(sessionId);
    let after = loaded.throughEventId ?? 0;
    let changed = false;
    for (let page = 0; page < 10; page += 1) {
      const events = await window.eidosRuntime.listEvents(sessionId, after);
      changed ||= events.items.length > 0;
      after = events.throughEventId;
      if (!events.hasMore) break;
    }
    if (changed) {
      loaded = await window.eidosRuntime.readSession(sessionId);
    }
    return loaded;
  }

  const loadSessions = useCallback(async (): Promise<void> => {
    setError(undefined);
    try {
      const page = await window.eidosRuntime.listSessions();
      setSessions(page.items);
    } catch (cause) {
      setError(userFacingError(cause));
    }
  }, []);

  const selectSession = useCallback(async (session: Session): Promise<SessionSnapshot | undefined> => {
    setNavigationSessionId(session.id);

    if (activeSelectionRef.current?.sessionId === session.id) {
      return activeSelectionRef.current.promise;
    }

    if (
      selectedSessionIdRef.current === session.id
      && snapshot?.session.id === session.id
    ) {
      if (session.taskStatus === "completed") {
        updateReadCompleted((prev) => new Set(prev).add(session.id));
      }
      return snapshot;
    }

    const tokenSymbol = Symbol("session_selection");
    const snapshotToken = snapshotReads.select(session.id);
    selectedSessionIdRef.current = session.id;
    if (session.taskStatus === "completed") {
      updateReadCompleted((prev) => new Set(prev).add(session.id));
    }
    setError(undefined);
    setPending((prev) => ({ ...prev, selectingSessionId: session.id }));

    const promise = (async (): Promise<SessionSnapshot | undefined> => {
      try {
        const loaded = await loadAuthoritativeSnapshot(session.id);
        if (activeSelectionRef.current?.token !== tokenSymbol) {
          return undefined;
        }
        const accepted = snapshotReads.accept(snapshotToken, loaded);
        if (accepted) {
          setSnapshot(accepted);
          setSessions((prev) => prev.map((s) => s.id === loaded.session.id ? loaded.session : s));
          return accepted;
        }
        return undefined;
      } catch (cause) {
        if (activeSelectionRef.current?.token === tokenSymbol && snapshotReads.isCurrent(snapshotToken)) {
          const fallback = snapshot?.session.id;
          selectedSessionIdRef.current = fallback;
          snapshotReads.select(fallback ?? "");
          setNavigationSessionId(fallback);
          setError(userFacingError(cause));
        }
        return undefined;
      } finally {
        if (activeSelectionRef.current?.token === tokenSymbol) {
          activeSelectionRef.current = undefined;
          clearPending("selectingSessionId");
        }
      }
    })();

    activeSelectionRef.current = {
      token: tokenSymbol,
      sessionId: session.id,
      promise,
    };

    return promise;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  const createSession = useCallback(async (workspaceRoot?: string): Promise<SessionSnapshot | undefined> => {
    if (creatingSessionRef.current) {
      return undefined;
    }
    creatingSessionRef.current = true;
    setPending((prev) => ({ ...prev, creatingSession: true }));
    setError(undefined);

    try {
      const workspace = workspaceRoot ?? await window.eidosRuntime.selectWorkspace();
      if (!workspace) return undefined;

      const session = await window.eidosRuntime.createSession(workspace);
      const token = snapshotReads.select(session.id);
      selectedSessionIdRef.current = session.id;
      setNavigationSessionId(session.id);
      setSessions((prev) => [session, ...prev]);
      const loaded = await loadAuthoritativeSnapshot(session.id);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
        return accepted;
      }
      return undefined;
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    } finally {
      creatingSessionRef.current = false;
      clearPending("creatingSession");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const renameSession = useCallback(async (sessionId: string, title: string): Promise<void> => {
    setPending((prev) => ({ ...prev, renamingSessionId: sessionId }));
    setError(undefined);
    try {
      const renamed = await window.eidosRuntime.renameSession(sessionId, title);
      setSessions((prev) => prev.map((s) => s.id === renamed.id ? renamed : s));
      setSnapshot((prev) => prev && ({ ...prev, session: renamed }));
    } catch (cause) {
      const msg = userFacingError(cause);
      setError(msg);
      throw cause;
    } finally {
      clearPending("renamingSessionId");
    }
  }, []);

  const deleteSession = useCallback(async (session: Session): Promise<{ confirmed: true } | { confirmed: false; error: string }> => {
    setPending((prev) => ({ ...prev, deletingSessionId: session.id }));
    setError(undefined);
    try {
      const deleted = await window.eidosRuntime.deleteSession(session.id);
      const remaining = sessions.filter((s) => s.id !== deleted.deletedSessionId);
      setSessions(remaining);
      updateReadCompleted((prev) => {
        const next = new Set(prev);
        next.delete(deleted.deletedSessionId);
        return next;
      });
      if (snapshot?.session.id === deleted.deletedSessionId) {
        setSnapshot(undefined);
        setNavigationSessionId(undefined);
        selectedSessionIdRef.current = undefined;
        snapshotReads.select("");
        if (remaining[0]) {
          await selectSession(remaining[0]);
        }
      }
      return { confirmed: true };
    } catch (cause) {
      const errMsg = userFacingError(cause);
      setError(errMsg);
      return { confirmed: false, error: errMsg };
    } finally {
      clearPending("deletingSessionId");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions, snapshot, selectSession]);

  const projectRun = useCallback((sessionId: string, run: Run): void => {
    const taskStatus = taskStatusFromRun(run);
    setSessions((prev) => prev.map((s) => s.id === sessionId
      ? { ...s, taskStatus, updatedAt: run.updatedAt }
      : s));
    setSnapshot((prev) => {
      if (!prev || prev.session.id !== sessionId) return prev;
      return { ...prev, runs: upsertRun(prev.runs, run) };
    });
  }, []);

  const handleTitleNotification = useCallback((params: { sessionId: string; title: string }): void => {
    setSessions((prev) => prev.map((s) => s.id === params.sessionId ? { ...s, title: params.title } : s));
    setSnapshot((prev) => {
      if (!prev || prev.session.id !== params.sessionId) return prev;
      return { ...prev, session: { ...prev.session, title: params.title } };
    });
  }, []);

  const handleRunNotification = useCallback((run: {
    id: string;
    sessionId: string;
    status: string;
    updatedAt: number;
  }): void => {
    const taskStatus = taskStatusFromRun({ status: run.status } as Parameters<typeof taskStatusFromRun>[0]);
    setSessions((prev) => prev.map((s) => s.id === run.sessionId
      ? { ...s, taskStatus, updatedAt: run.updatedAt }
      : s));
    updateReadCompleted((prev) => {
      const next = new Set(prev);
      if (run.status === "succeeded" && selectedSessionIdRef.current === run.sessionId) {
        next.add(run.sessionId);
      } else if (["queued", "running", "waiting_approval", "waiting_user_input", "finalizing", "succeeded"].includes(run.status)) {
        next.delete(run.sessionId);
      }
      return next;
    });
  }, []);

  const refreshCompletedSession = useCallback(async (sessionId: string): Promise<void> => {
    const token = snapshotReads.refresh(sessionId);
    if (!token) return;
    try {
      const loaded = await loadAuthoritativeSnapshot(sessionId);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
        setSessions((prev) => prev.map((s) => s.id === loaded.session.id ? loaded.session : s));
      }
    } catch (cause) {
      if (snapshotReads.isCurrent(token)) {
        setError(userFacingError(cause));
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const state: SessionControllerState = {
    sessions,
    snapshot,
    navigationSessionId,
    readCompletedSessions,
    pending,
    error,
  };

  const actions: SessionControllerActions = {
    loadSessions,
    selectSession,
    createSession,
    renameSession,
    deleteSession,
    setError,
    projectRun,
    handleTitleNotification,
    handleRunNotification,
    refreshCompletedSession,
    setSnapshot,
    setSessions,
  };

  return [state, actions];
}
