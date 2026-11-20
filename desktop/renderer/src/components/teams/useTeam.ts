import { useCallback, useEffect, useRef, useState } from "react";
import type { TeamMethods, TeamSessionState } from "../../../../shared/teams.js";
import { userFacingError } from "../../session-state.js";

type Mutation = Exclude<keyof TeamMethods, "team/read" | "team/messages">;
type MutationRequest<K extends Mutation> = Omit<TeamMethods[K]["request"], "operationId">;
interface TeamState { sessionId: string | undefined; view: TeamSessionState; loaded: boolean; error: string | undefined }

const REFRESH_ON = new Set(["team/updated", "run/started", "run/updated", "run/completed"]);

/** Team state for one Session; `team/updated` and lead Run edges refresh it. */
export function useTeam(sessionId: string | undefined, ready: boolean) {
  const [state, setState] = useState<TeamState>({ sessionId: undefined, view: {}, loaded: false, error: undefined });
  const [busySession, setBusySession] = useState<string>();
  const generation = useRef(0);
  const mounted = useRef(true);
  const currentSession = useRef(sessionId);
  currentSession.current = sessionId;
  // Member Runs belong to internal Sessions; their edges refresh the lead's view.
  const memberSessions = useRef(new Set<string>());
  const refresh = useCallback(async () => {
    if (!ready || !sessionId) return undefined;
    const token = ++generation.current;
    try {
      const view = await window.eidosRuntime.team("team/read", { sessionId });
      if (mounted.current && currentSession.current === sessionId && token === generation.current) {
        setState((previous) => ({ sessionId, view, loaded: true, error: previous.sessionId === sessionId ? previous.error : undefined }));
      }
      return view;
    } catch (cause) {
      if (mounted.current && currentSession.current === sessionId && token === generation.current) {
        setState((previous) => ({ sessionId, view: previous.sessionId === sessionId ? previous.view : {}, loaded: true,
          error: userFacingError(cause) }));
      }
      return undefined;
    }
  }, [sessionId, ready]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    if (!ready || !sessionId) return () => { mounted.current = false; generation.current++; };
    const unsubscribe = window.eidosRuntime.onNotification((notice) => {
      if (!REFRESH_ON.has(notice.method) || !("sessionId" in notice.params)) return;
      const target = notice.params.sessionId;
      if (target === sessionId || notice.method !== "team/updated" && memberSessions.current.has(target)) void refresh();
    });
    return () => { mounted.current = false; generation.current++; unsubscribe(); };
  }, [refresh, ready, sessionId]);
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
  const visible = state.sessionId === sessionId ? state : undefined;
  const view = visible?.view ?? {};
  memberSessions.current = new Set(view.team?.members?.map((member) => member.sessionId) ?? []);
  return {
    team: view.team ?? undefined, proposal: view.proposal ?? undefined, usage: view.usage ?? [], loaded: visible?.loaded ?? false,
    busy: busySession === sessionId && !!sessionId, error: visible?.error, execute, refresh,
  };
}
