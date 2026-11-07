import { useCallback, useEffect, useRef, useState } from "react";
import type { GoalRequest, GoalView } from "../../../../shared/goals.js";
import { userFacingError } from "../../session-state.js";

interface GoalState { sessionId: string | undefined; view: GoalView; loaded: boolean; error: string | undefined }

export function useGoal(sessionId: string | undefined, ready: boolean) {
  const [state, setState] = useState<GoalState>({ sessionId: undefined, view: {}, loaded: false, error: undefined });
  const [savingSession, setSavingSession] = useState<string>();
  const generation = useRef(0);
  const mounted = useRef(true);
  const currentSession = useRef(sessionId);
  currentSession.current = sessionId;
  const saving = useRef(false);
  const refresh = useCallback(async (clearError = false) => {
    if (!ready || !sessionId) return;
    const token = ++generation.current;
    try {
      const result = await window.eidosRuntime.goal({ action: "get", sessionId });
      if (mounted.current && currentSession.current === sessionId && token === generation.current) {
        setState((previous) => ({ sessionId, view: result, loaded: true,
          error: clearError || previous.sessionId !== sessionId ? undefined : previous.error }));
        return result;
      }
    } catch (cause) {
      if (mounted.current && currentSession.current === sessionId && token === generation.current) {
        setState((previous) => ({ sessionId, view: previous.sessionId === sessionId ? previous.view : {}, loaded: true, error: userFacingError(cause) }));
      }
    }
  }, [sessionId, ready]);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    if (!ready || !sessionId) return () => { mounted.current = false; generation.current++; };
    const unsubscribe = window.eidosRuntime.onNotification((notice) => {
      if ("sessionId" in notice.params && notice.params.sessionId === sessionId
          && ["goal/updated", "run/started", "run/updated", "run/completed", "item/completed"].includes(notice.method)) void refresh();
    });
    return () => { mounted.current = false; generation.current++; unsubscribe(); };
  }, [refresh, ready, sessionId]);
  const execute = async (request: Omit<GoalRequest, "sessionId" | "operationId">) => {
    if (saving.current || !ready || !sessionId) return false;
    saving.current = true;
    setSavingSession(sessionId);
    setState((previous) => ({ ...previous, error: undefined }));
    try {
      const result = await window.eidosRuntime.goal({ ...request, sessionId, operationId: crypto.randomUUID() });
      if (mounted.current && currentSession.current === sessionId) {
        generation.current++; setState({ sessionId, view: result, loaded: true, error: undefined });
      }
      void refresh();
      return true;
    } catch (cause) {
      if (mounted.current && currentSession.current === sessionId) setState((previous) => ({ ...previous, error: userFacingError(cause) }));
      void refresh();
      return false;
    } finally {
      saving.current = false;
      if (mounted.current) setSavingSession(undefined);
    }
  };
  const visible = state.sessionId === sessionId ? state : undefined;
  return { view: visible?.view ?? {}, loaded: visible?.loaded ?? false, busy: savingSession === sessionId && !!sessionId,
    error: visible?.error, execute, refresh };
}
