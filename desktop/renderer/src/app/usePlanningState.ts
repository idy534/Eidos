import { useEffect, useRef, useState } from "react";
import type { PlanDocument, PlanningReadResponse, UserInputRequest } from "../../../shared/planning.generated.js";
import { userFacingError } from "../session-state.js";

export interface PlanningStateResult {
  state: PlanningReadResponse | undefined;
  pendingQuestion: UserInputRequest | undefined;
  plan: PlanDocument | undefined;
  plans: PlanDocument[];
  historyQuestions: UserInputRequest[];
  error: string;
  refresh: () => void;
}

export function usePlanningState(
  sessionId: string | undefined,
  ready: boolean,
): PlanningStateResult {
  const [loaded, setLoaded] = useState<{
    sessionId: string;
    state: PlanningReadResponse | undefined;
    error: string;
  }>();
  const current = ready && loaded?.sessionId === sessionId ? loaded : undefined;
  const state = current?.state;
  const error = current?.error ?? "";
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (!sessionId || !ready || typeof window.eidosRuntime?.readPlanning !== "function") {
      setLoaded(undefined);
      return;
    }

    setLoaded(undefined);
    let disposed = false;
    let generation = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = () => {
      const current = ++generation;
      const readFn = window.eidosRuntime?.readPlanning;
      if (typeof readFn !== "function") return;

      void readFn(sessionId)
        .then((value) => {
          if (!disposed && current === generation) {
            setLoaded({sessionId, state: value, error: ""});
          }
        })
        .catch((cause: unknown) => {
          if (!disposed && current === generation) {
            setLoaded((previous) => ({
              sessionId,
              state: previous?.sessionId === sessionId ? previous.state : undefined,
              error: userFacingError(cause),
            }));
          }
        });
    };

    refreshRef.current = load;
    load();

    const unsubscribe =
      typeof window.eidosRuntime?.onNotification === "function"
        ? window.eidosRuntime.onNotification(() => {
            if (timer !== undefined) return;
            timer = setTimeout(() => {
              timer = undefined;
              load();
            }, 250);
          })
        : () => {};

    return () => {
      disposed = true;
      clearTimeout(timer);
      unsubscribe();
      refreshRef.current = () => {};
    };
  }, [sessionId, ready]);

  const pendingQuestion = state?.questions.find((request) => request.status === "pending");
  const plan = state?.plans[0];
  const plans = state?.plans ?? [];
  const historyQuestions = state?.questions.filter((request) => request.status !== "pending") ?? [];

  return {
    state,
    pendingQuestion,
    plan,
    plans,
    historyQuestions,
    error,
    refresh: () => refreshRef.current(),
  };
}
