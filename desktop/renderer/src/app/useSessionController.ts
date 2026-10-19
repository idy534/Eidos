import type { SessionForkRequest } from "../../../shared/session-fork.generated.js";
import { useCallback, useRef, useState } from "react";
import type {
  CreateBranchResult,
  Project,
  Run,
  Session,
  SessionGitMutationResult,
  SessionSnapshot,
} from "../contracts.js";
import { SnapshotReadCoordinator, taskStatusFromRun, upsertRun, userFacingError } from "../session-state.js";
import { mergeContextCompactions } from "../context-compaction.js";

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
  creatingBranchSessionId?: string;
  branchSessionId?: string;
  selectingSessionId?: string;
  loadingHistorySessionId?: string;
  renamingSessionId?: string;
  deletingSessionId?: string;
  handoffSessionId?: string;
  restoringWorktreeSessionId?: string;
}

export interface SessionControllerState {
  sessions: Session[];
  projects: Project[];
  snapshot: SessionSnapshot | undefined;
  draft: SessionSnapshot | undefined;
  navigationSessionId: string | undefined;
  readCompletedSessions: ReadonlySet<string>;
  pending: PendingOperations;
  error: string | undefined;
}

export interface SessionControllerActions {
  loadProjects: () => Promise<void>;
  loadSessions: () => Promise<void>;
  createProject: (name: string | undefined, workspaceRoot: string) => Promise<Project | undefined>;
  startDraft: (project?: Project | null) => void;
  updateDraftExecutionMode: (mode: "local" | "worktree") => void;
  materializeDraft: (workspaceNameHint?: string) => Promise<SessionSnapshot | undefined>;
  discardDraft: () => void;
  rollbackMaterializedSession: (session: Session, draft: SessionSnapshot) => Promise<boolean>;
  selectSession: (session: Session, focusItemId?: string) => Promise<SessionSnapshot | undefined>;
  loadOlderSession: () => Promise<void>;
  createSession: (
    workspaceRoot?: string | null,
    options?: {
      workspaceNameHint?: string;
      executionMode?: "local" | "worktree";
      baseRef?: string;
      includeLocalChanges?: boolean;
    },
  ) => Promise<SessionSnapshot | undefined>;
  forkSession: (sourceSessionId: string, sourceItemId: string, target: SessionForkRequest["target"]) => Promise<SessionSnapshot | undefined>;
  createSessionBranch: (
    sessionId: string,
    branch: string,
  ) => Promise<CreateBranchResult | undefined>;
  switchLocalBranch: (
    sessionId: string,
    branch: string,
  ) => Promise<SessionGitMutationResult | undefined>;
  createLocalBranch: (
    sessionId: string,
    branch: string,
  ) => Promise<SessionGitMutationResult | undefined>;
  handoffSession: (
    sessionId: string,
    target: "local" | "worktree",
  ) => Promise<SessionSnapshot | undefined>;
  restoreWorktree: (sessionId: string) => Promise<SessionSnapshot | undefined>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  deleteSession: (session: Session) => Promise<{ confirmed: true } | { confirmed: false; error: string }>;
  deleteProject: (project: Project) => Promise<{ confirmed: true } | { confirmed: false; error: string }>;
  setError: (error: string | undefined) => void;
  projectRun: (sessionId: string, run: Run) => void;
  /** Called when a session title notification arrives — updates sessions title and open snapshot */
  handleTitleNotification: (params: { sessionId: string; title: string }) => void;
  /** Called when a run notification arrives — updates sessions taskStatus */
  handleRunNotification: (run: Pick<Run, "id" | "sessionId" | "status" | "updatedAt" | "reconciliationRequired">) => void;
  /** Called when a session completes — triggers authoritative snapshot refresh */
  refreshCompletedSession: (sessionId: string) => Promise<void>;
  setSnapshot: (updater: (prev: SessionSnapshot | undefined) => SessionSnapshot | undefined) => void;
  setSessions: (sessions: Session[]) => void;
}

interface SessionSelectionOperation {
  token: symbol;
  sessionId: string;
  focusItemId: string | undefined;
  promise: Promise<SessionSnapshot | undefined>;
}

function mergeOlderHistory(current: SessionSnapshot, older: SessionSnapshot): SessionSnapshot {
  if (older.session.id !== current.session.id) throw new Error("History scope mismatch");
  const { previousItemId: _cursor, ...base } = current;
  const history = [...new Map([...(older.inheritedHistory ?? []), ...(current.inheritedHistory ?? [])].map((entry) => [entry.item.id, entry])).values()];
  return {
    ...base,
    runs: [...new Map([...older.runs, ...current.runs].map((run) => [run.id, run])).values()],
    items: [...new Map([...older.items, ...current.items].map((item) => [item.id, item])).values()],
    contextCompactions: mergeContextCompactions(older.contextCompactions, current.contextCompactions),
    ...(older.previousItemId ? { previousItemId: older.previousItemId } : {}),
    ...(history.length ? { inheritedHistory: history } : {}),
  };
}

export function useSessionController(): [SessionControllerState, SessionControllerActions] {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | undefined>(undefined);
  const [draft, setDraft] = useState<SessionSnapshot | undefined>(undefined);
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
  const selectionEpochRef = useRef(0);
  const historyLoadingRef = useRef<{ sessionId: string; epoch: number } | undefined>(undefined);
  const sessionListLoadRef = useRef(0);

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
    const loadId = ++sessionListLoadRef.current;
    setError(undefined);
    try {
      const loaded: Session[] = [];
      const seenCursors = new Set<string>();
      let cursor: string | undefined;
      do {
        const page = cursor === undefined
          ? await window.eidosRuntime.listSessions()
          : await window.eidosRuntime.listSessions(cursor);
        if (loadId !== sessionListLoadRef.current) return;
        loaded.push(...page.items);
        cursor = page.nextCursor;
        if (cursor !== undefined) {
          if (!cursor || seenCursors.has(cursor)) throw new Error("Invalid Session list cursor");
          seenCursors.add(cursor);
        }
      } while (cursor !== undefined);
      setSessions(loaded);
    } catch (cause) {
      if (loadId === sessionListLoadRef.current) setError(userFacingError(cause));
    }
  }, []);

  const loadProjects = useCallback(async (): Promise<void> => {
    setError(undefined);
    try {
      const result = await window.eidosRuntime.listProjects();
      setProjects(result.items);
    } catch (cause) {
      setError(userFacingError(cause));
    }
  }, []);

  const createProject = useCallback(async (
    name: string | undefined,
    workspaceRoot: string,
  ): Promise<Project | undefined> => {
    setError(undefined);
    try {
      const project = await window.eidosRuntime.createProject(name, workspaceRoot);
      setProjects((prev) => [project, ...prev.filter((item) => item.id !== project.id)]);
      return project;
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    }
  }, []);

  const startDraft = useCallback((project?: Project | null): void => {
    const session: Session = {
      id: `draft-${crypto.randomUUID()}`,
      workspaceRoot: project?.workspaceRoot ?? "",
      ...(project
        ? {
            project: {
              id: project.id,
              ...(project.name ? { name: project.name } : {}),
              workspaceRoot: project.workspaceRoot,
              gitAvailable: project.gitAvailable,
            },
          }
        : { projectless: true }),
      executionMode: "local",
      taskStatus: "new",
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    setError(undefined);
    setDraft({ session, runs: [], items: [], stepResolutions: [] });
    setSnapshot(undefined);
    setNavigationSessionId(undefined);
    selectedSessionIdRef.current = undefined;
    snapshotReads.select("");
  }, [snapshotReads]);

  const updateDraftExecutionMode = useCallback((mode: "local" | "worktree"): void => {
    setDraft((prev) => prev
      ? { ...prev, session: { ...prev.session, executionMode: mode, updatedAt: Date.now() } }
      : prev);
  }, []);

  const selectSession = useCallback(async (session: Session, focusItemId?: string): Promise<SessionSnapshot | undefined> => {
    setDraft(undefined);
    setNavigationSessionId(session.id);

    if (activeSelectionRef.current?.sessionId === session.id && activeSelectionRef.current.focusItemId === focusItemId) {
      return activeSelectionRef.current.promise;
    }

    if (
      selectedSessionIdRef.current === session.id
      && snapshot?.session.id === session.id
      && !focusItemId
    ) {
      if (session.taskStatus === "completed") {
        updateReadCompleted((prev) => new Set(prev).add(session.id));
      }
      return snapshot;
    }

    const tokenSymbol = Symbol("session_selection");
    selectionEpochRef.current += 1;
    const snapshotToken = snapshotReads.select(session.id);
    selectedSessionIdRef.current = session.id;
    if (session.taskStatus === "completed") {
      updateReadCompleted((prev) => new Set(prev).add(session.id));
    }
    setError(undefined);
    setPending((prev) => ({ ...prev, selectingSessionId: session.id }));

    const promise = (async (): Promise<SessionSnapshot | undefined> => {
      try {
        let loaded = await loadAuthoritativeSnapshot(session.id);
        if (activeSelectionRef.current?.token !== tokenSymbol) return undefined;
        const seenCursors = new Set<string>();
        while (focusItemId && !loaded.items.some((item) => item.id === focusItemId)) {
          const cursor = loaded.previousItemId;
          if (!cursor || seenCursors.has(cursor) || loaded.items.length >= 20_000) throw new Error("Source answer unavailable");
          seenCursors.add(cursor);
          const older = await window.eidosRuntime.readSession(session.id, { itemLimit: 500, beforeItemId: cursor });
          if (activeSelectionRef.current?.token !== tokenSymbol) return undefined;
          loaded = mergeOlderHistory(loaded, older);
        }
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
      focusItemId,
      promise,
    };

    return promise;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  const loadOlderSession = useCallback(async (): Promise<void> => {
    if (!snapshot?.previousItemId) return;
    const sessionId = snapshot.session.id;
    const epoch = selectionEpochRef.current;
    if (historyLoadingRef.current?.sessionId === sessionId && historyLoadingRef.current.epoch === epoch) return;
    const operation = { sessionId, epoch };
    historyLoadingRef.current = operation;
    setPending((prev) => ({ ...prev, loadingHistorySessionId: sessionId }));
    try {
      const older = await window.eidosRuntime.readSession(sessionId, { itemLimit: 500, beforeItemId: snapshot.previousItemId });
      if (older.session.id !== sessionId || older.previousItemId === snapshot.previousItemId) throw new Error("Invalid history page");
      if (selectedSessionIdRef.current === sessionId && selectionEpochRef.current === epoch) {
        setSnapshot((current) => current?.session.id === sessionId ? mergeOlderHistory(current, older) : current);
      }
    } catch (cause) {
      if (selectedSessionIdRef.current === sessionId && selectionEpochRef.current === epoch) setError(userFacingError(cause));
    } finally {
      if (historyLoadingRef.current === operation) {
        historyLoadingRef.current = undefined;
        clearPending("loadingHistorySessionId");
      }
    }
  }, [snapshot]);

  const createSession = useCallback(async (
    workspaceRoot?: string | null,
    options: {
      workspaceNameHint?: string;
      executionMode?: "local" | "worktree";
      baseRef?: string;
      includeLocalChanges?: boolean;
    } = {},
  ): Promise<SessionSnapshot | undefined> => {
    if (creatingSessionRef.current) {
      return undefined;
    }
    creatingSessionRef.current = true;
    setPending((prev) => ({ ...prev, creatingSession: true }));
    setError(undefined);

    try {
      const session = Object.keys(options).length > 0
        ? await window.eidosRuntime.createSession(workspaceRoot ?? null, options)
        : await window.eidosRuntime.createSession(workspaceRoot ?? null);
      const token = snapshotReads.select(session.id);
      selectedSessionIdRef.current = session.id;
      setNavigationSessionId(session.id);
      setSessions((prev) => [session, ...prev]);
      const project = session.project;
      if (project && session.projectless !== true) {
        setProjects((prev) => {
          const existing = prev.find((item) => item.id === project.id);
          const next: Project = {
            ...project,
            ...(project.name
              ? { name: project.name }
              : existing?.name
                ? { name: existing.name }
                : {}),
            createdAt: existing?.createdAt ?? session.createdAt,
            updatedAt: existing?.updatedAt ?? session.updatedAt,
          };
          return existing
            ? prev.map((project) => project.id === next.id ? next : project)
            : [...prev, next];
        });
      }
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

  const materializeDraft = useCallback(async (workspaceNameHint?: string): Promise<SessionSnapshot | undefined> => {
    const currentDraft = draft;
    if (!currentDraft) return undefined;
    const projectRoot = currentDraft.session.project?.workspaceRoot ?? null;
    return createSession(projectRoot, {
      executionMode: currentDraft.session.executionMode ?? "local",
      ...(projectRoot === null ? { workspaceNameHint: workspaceNameHint ?? "新会话" } : {}),
    });
  }, [createSession, draft]);

  const discardDraft = useCallback((): void => {
    setDraft(undefined);
  }, []);

  const rollbackMaterializedSession = useCallback(async (
    session: Session,
    draftSnapshot: SessionSnapshot,
  ): Promise<boolean> => {
    try {
      const deleted = await window.eidosRuntime.deleteSession(session.id);
      setSessions((prev) => prev.filter((item) => item.id !== deleted.deletedSessionId));
      setSnapshot((prev) => prev?.session.id === deleted.deletedSessionId ? undefined : prev);
      setDraft(draftSnapshot);
      setNavigationSessionId(undefined);
      selectedSessionIdRef.current = undefined;
      snapshotReads.select("");
      return true;
    } catch (cause) {
      setError(userFacingError(cause));
      return false;
    }
  }, [snapshotReads]);

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

  const forkOperations = useRef(new Map<string, string>());
  const forkSession = useCallback(async (sourceSessionId: string, sourceItemId: string, target: SessionForkRequest["target"]): Promise<SessionSnapshot | undefined> => {
    if (creatingSessionRef.current) return undefined;
    creatingSessionRef.current = true;
    setPending((prev) => ({ ...prev, creatingSession: true }));
    setError(undefined);
    const key = `${sourceSessionId}:${sourceItemId}:${target.kind}`;
    let operationId = forkOperations.current.get(key);
    if (!operationId) {
      operationId = crypto.randomUUID();
      forkOperations.current.set(key, operationId);
    }
    try {
      const session = await window.eidosRuntime.forkSession({ sourceSessionId, sourceItemId, target, operationId });
      setSessions((prev) => [session, ...prev.filter((existing) => existing.id !== session.id)]);
      const loaded = await selectSession(session);
      forkOperations.current.delete(key);
      return loaded;
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    } finally {
      creatingSessionRef.current = false;
      setPending((prev) => ({ ...prev, creatingSession: false }));
    }
  }, [selectSession]);

  const createSessionBranch = useCallback(async (
    sessionId: string,
    branch: string,
  ): Promise<CreateBranchResult | undefined> => {
    setPending((prev) => ({ ...prev, creatingBranchSessionId: sessionId }));
    setError(undefined);
    try {
      const result = await window.eidosRuntime.createSessionBranch(sessionId, branch);
      const loaded = await loadAuthoritativeSnapshot(sessionId);
      setSessions((prev) => prev.map((session) => (
        session.id === loaded.session.id ? loaded.session : session
      )));
      if (snapshot?.session.id === sessionId) {
        setSnapshot(loaded);
      }
      return result;
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    } finally {
      clearPending("creatingBranchSessionId");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  const switchLocalBranch = useCallback(async (
    sessionId: string,
    branch: string,
  ): Promise<SessionGitMutationResult | undefined> => {
    setPending((prev) => ({ ...prev, branchSessionId: sessionId }));
    setError(undefined);
    try {
      return await window.eidosRuntime.switchSessionGitBranch(
        sessionId, branch, crypto.randomUUID(),
      );
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    } finally {
      clearPending("branchSessionId");
    }
  }, []);

  const createLocalBranch = useCallback(async (
    sessionId: string,
    branch: string,
  ): Promise<SessionGitMutationResult | undefined> => {
    setPending((prev) => ({ ...prev, creatingBranchSessionId: sessionId }));
    setError(undefined);
    try {
      return await window.eidosRuntime.createSessionGitBranch(
        sessionId, branch, crypto.randomUUID(),
      );
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    } finally {
      clearPending("creatingBranchSessionId");
    }
  }, []);

  const handoffSession = useCallback(async (
    sessionId: string,
    target: "local" | "worktree",
  ): Promise<SessionSnapshot | undefined> => {
    setPending((prev) => ({ ...prev, handoffSessionId: sessionId }));
    setError(undefined);
    try {
      await window.eidosRuntime.handoffSession(sessionId, target);
      const loaded = await loadAuthoritativeSnapshot(sessionId);
      setSessions((prev) => prev.map((session) => (
        session.id === loaded.session.id ? loaded.session : session
      )));
      if (snapshot?.session.id === sessionId) {
        setSnapshot(loaded);
      }
      return loaded;
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    } finally {
      clearPending("handoffSessionId");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  const restoreWorktree = useCallback(async (
    sessionId: string,
  ): Promise<SessionSnapshot | undefined> => {
    setPending((prev) => ({ ...prev, restoringWorktreeSessionId: sessionId }));
    setError(undefined);
    try {
      await window.eidosRuntime.restoreSessionWorktree(sessionId);
      const loaded = await loadAuthoritativeSnapshot(sessionId);
      setSessions((prev) => prev.map((session) => (
        session.id === loaded.session.id ? loaded.session : session
      )));
      if (snapshot?.session.id === sessionId) {
        setSnapshot(loaded);
      }
      return loaded;
    } catch (cause) {
      setError(userFacingError(cause));
      return undefined;
    } finally {
      clearPending("restoringWorktreeSessionId");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

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

  const deleteProject = useCallback(async (project: Project): Promise<{ confirmed: true } | { confirmed: false; error: string }> => {
    setError(undefined);
    try {
      const deleted = await window.eidosRuntime.deleteProject(project.id);
      setProjects((prev) => prev.filter((item) => item.id !== deleted.deletedProjectId));
      return { confirmed: true };
    } catch (cause) {
      const errMsg = userFacingError(cause);
      setError(errMsg);
      return { confirmed: false, error: errMsg };
    }
  }, []);

  const projectRun = useCallback((sessionId: string, run: Run): void => {
    const taskStatus = taskStatusFromRun(run);
    setSessions((prev) => prev.map((s) => s.id === sessionId
      ? { ...s, taskStatus, activeRunStatus: activeStatus(run.status), updatedAt: run.updatedAt }
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

  const handleRunNotification = useCallback((run: Pick<Run, "id" | "sessionId" | "status" | "updatedAt" | "reconciliationRequired">): void => {
    const taskStatus = taskStatusFromRun(run);
    void loadSessions();
    setSessions((prev) => prev.map((s) => s.id === run.sessionId
      ? { ...s, taskStatus, activeRunStatus: activeStatus(run.status), updatedAt: run.updatedAt }
      : s));
    updateReadCompleted((prev) => {
      const next = new Set(prev);
      if (
        run.status === "succeeded"
        && selectedSessionIdRef.current === run.sessionId
      ) {
        next.add(run.sessionId);
      } else if ([
        "queued", "running", "waiting_input", "waiting_agents", "waiting_approval", "finalizing", "succeeded",
      ].includes(run.status)) {
        next.delete(run.sessionId);
      }
      return next;
    });
  }, [loadSessions]);

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
    projects,
    snapshot,
    draft,
    navigationSessionId,
    readCompletedSessions,
    pending,
    error,
  };

  const actions: SessionControllerActions = {
    loadProjects,
    loadSessions,
    createProject,
    startDraft,
    updateDraftExecutionMode,
    materializeDraft,
    discardDraft,
    rollbackMaterializedSession,
    selectSession,
    loadOlderSession,
    createSession,
    forkSession,
    createSessionBranch,
    switchLocalBranch,
    createLocalBranch,
    handoffSession,
    restoreWorktree,
    renameSession,
    deleteSession,
    deleteProject,
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

function activeStatus(status: Run["status"]): Session["activeRunStatus"] {
  return status === "queued" || status === "running" || status === "waiting_input" || status === "waiting_agents" || status === "waiting_approval" || status === "finalizing" ? status : undefined;
}
