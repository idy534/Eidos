import { useCallback, useEffect, useRef, useState } from "react";
import type { TeamMethods, TeamSessionState } from "../../../../shared/teams.js";
import { userFacingError } from "../../session-state.js";

type Mutation = Exclude<keyof TeamMethods, "team/read" | "team/messages">;
type MutationRequest<K extends Mutation> = Omit<TeamMethods[K]["request"], "operationId">;
interface TeamState {
  sessionId: string | undefined;
  view: TeamSessionState;
  loaded: boolean;
  error: string | undefined;
  revision: number;
}

const IMMEDIATE_REFRESH_ON = new Set(["team/updated", "run/started", "run/updated", "run/completed"]);
const COALESCE_REFRESH_ON = new Set(["item/completed"]);
const COALESCE_DELAY_MS = 500;
const ACTIVE_POLL_MS = 4000;

/** Team state for one Session; refreshes on team/Run/step events, foreground resume, active polling and manual calls. */
export function useTeam(sessionId: string | undefined, ready: boolean) {
  const [state, setState] = useState<TeamState>({
    sessionId: undefined, view: {}, loaded: false, error: undefined, revision: 0,
  });
  const [busySession, setBusySession] = useState<string>();
  const generation = useRef(0);
  const mounted = useRef(true);
  const currentSession = useRef(sessionId);
  currentSession.current = sessionId;
  // Member Runs belong to internal Sessions; their edges refresh the lead's view.
  const memberSessions = useRef(new Set<string>());
  const coalesceTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const clearCoalesceTimer = useCallback(() => {
    if (coalesceTimer.current !== undefined) {
      clearTimeout(coalesceTimer.current);
      coalesceTimer.current = undefined;
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!ready || !sessionId) return undefined;
    clearCoalesceTimer();
    const token = ++generation.current;
    try {
      const view = await window.eidosRuntime.team("team/read", { sessionId });
      if (mounted.current && currentSession.current === sessionId && token === generation.current) {
        setState((previous) => ({
          sessionId,
          view,
          loaded: true,
          error: previous.sessionId === sessionId ? previous.error : undefined,
          revision: previous.revision + 1,
        }));
      }
      return view;
    } catch (cause) {
      if (mounted.current && currentSession.current === sessionId && token === generation.current) {
        setState((previous) => ({
          sessionId,
          view: previous.sessionId === sessionId ? previous.view : {},
          loaded: true,
          error: userFacingError(cause),
          revision: previous.revision + 1,
        }));
      }
      return undefined;
    }
  }, [sessionId, ready, clearCoalesceTimer]);

  const scheduleRefresh = useCallback(() => {
    if (coalesceTimer.current !== undefined) return;
    coalesceTimer.current = setTimeout(() => {
      coalesceTimer.current = undefined;
      void refresh();
    }, COALESCE_DELAY_MS);
  }, [refresh]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    if (!ready || !sessionId) {
      return () => {
        mounted.current = false;
        generation.current++;
        clearCoalesceTimer();
      };
    }
    const unsubscribe = window.eidosRuntime.onNotification((notice) => {
      if (!("sessionId" in notice.params)) return;
      const target = notice.params.sessionId;
      const matchesSession = target === sessionId || memberSessions.current.has(target);
      if (!matchesSession) return;
      if (IMMEDIATE_REFRESH_ON.has(notice.method)) {
        if (notice.method === "team/updated" && target !== sessionId) return;
        void refresh();
      } else if (COALESCE_REFRESH_ON.has(notice.method)) {
        scheduleRefresh();
      }
    });
    const onVisible = () => {
      if (typeof document !== "undefined" && !document.hidden) void refresh();
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible);
    }
    return () => {
      mounted.current = false;
      generation.current++;
      clearCoalesceTimer();
      unsubscribe();
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisible);
      }
    };
  }, [refresh, scheduleRefresh, clearCoalesceTimer, ready, sessionId]);

  const visible = state.sessionId === sessionId ? state : undefined;
  const view = visible?.view ?? {};
  const teamStatus = view.team?.status;
  const shouldPoll = Boolean(ready && sessionId && (teamStatus === "active" || teamStatus === "dissolving"));

  useEffect(() => {
    if (!shouldPoll) return;
    const interval = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) {
        void refresh();
      }
    }, ACTIVE_POLL_MS);
    return () => clearInterval(interval);
  }, [shouldPoll, refresh]);

  const execute = async <K extends Mutation>(method: K, request: MutationRequest<K>): Promise<boolean> => {
    if (!ready || !sessionId || busySession === sessionId) return false;
    setBusySession(sessionId);
    setState((previous) => ({ ...previous, error: undefined }));
    try {
      await window.eidosRuntime.team(method, { ...request, operationId: crypto.randomUUID() } as TeamMethods[K]["request"]);
      return true;
    } catch (cause) {
      if (mounted.current && currentSession.current === sessionId) setState((previous) => ({ ...previous, error: userFacingError(cause) }));
      return false;
    } finally {
      if (mounted.current) setBusySession(undefined);
      void refresh();
    }
  };

  memberSessions.current = new Set(view.team?.members?.map((member) => member.sessionId) ?? []);
  return {
    team: view.team ?? undefined,
    proposal: view.proposal ?? undefined,
    usage: view.usage ?? [],
    loaded: visible?.loaded ?? false,
    revision: visible?.revision ?? 0,
    busy: busySession === sessionId && !!sessionId,
    error: visible?.error,
    execute,
    refresh,
  };
}
