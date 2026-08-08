import { useCallback, useRef, useState } from "react";

import type {
  ResponseActionState,
  ResponseFeedbackValue,
  RunRevisionResult,
} from "../contracts.js";
import { userFacingError } from "../session-state.js";


const EMPTY_STATE: ResponseActionState = { feedback: [], revisions: [] };

export interface ResponseActionControllerState {
  responseState: ResponseActionState;
  pendingFeedbackItemIds: ReadonlySet<string>;
  loadingSessionId: string | undefined;
  error: string | undefined;
}

export interface ResponseActionControllerActions {
  load: (sessionId: string) => Promise<void>;
  setFeedback: (
    sessionId: string,
    itemId: string,
    feedback: ResponseFeedbackValue | null,
  ) => Promise<void>;
  projectRevision: (sessionId: string, revision: RunRevisionResult) => void;
  clear: () => void;
}

export function useResponseActionController(
  currentSessionId: string | undefined,
): [ResponseActionControllerState, ResponseActionControllerActions] {
  const [states, setStates] = useState<Record<string, ResponseActionState>>({});
  const [pendingFeedbackItemIds, setPendingFeedbackItemIds] = useState<Set<string>>(new Set());
  const [loadingSessionId, setLoadingSessionId] = useState<string | undefined>(undefined);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const loadTokenRef = useRef(0);

  const load = useCallback(async (sessionId: string): Promise<void> => {
    const token = ++loadTokenRef.current;
    setLoadingSessionId(sessionId);
    setErrors((prev) => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
    try {
      const state = await window.eidosRuntime.readResponseActionState(sessionId);
      if (loadTokenRef.current !== token) return;
      setStates((prev) => ({ ...prev, [sessionId]: state }));
    } catch (cause) {
      if (loadTokenRef.current !== token) return;
      setErrors((prev) => ({ ...prev, [sessionId]: userFacingError(cause) }));
    } finally {
      if (loadTokenRef.current === token) {
        setLoadingSessionId(undefined);
      }
    }
  }, []);

  const setFeedback = useCallback(async (
    sessionId: string,
    itemId: string,
    feedback: ResponseFeedbackValue | null,
  ): Promise<void> => {
    if (pendingFeedbackItemIds.has(itemId)) return;

    const previous = states[sessionId] ?? EMPTY_STATE;
    const previousFeedback = previous.feedback.find((entry) => entry.itemId === itemId)?.value;
    const nextFeedback = previous.feedback.filter((entry) => entry.itemId !== itemId);
    if (feedback !== null) nextFeedback.push({ itemId, value: feedback });

    setPendingFeedbackItemIds((prev) => new Set(prev).add(itemId));
    setStates((prev) => ({
      ...prev,
      [sessionId]: {
        ...(prev[sessionId] ?? EMPTY_STATE),
        feedback: nextFeedback,
      },
    }));
    setErrors((prev) => {
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });

    try {
      await window.eidosRuntime.setItemFeedback(itemId, feedback);
    } catch (cause) {
      setStates((prev) => {
        const current = prev[sessionId] ?? EMPTY_STATE;
        const rolledBack = current.feedback.filter((entry) => entry.itemId !== itemId);
        if (previousFeedback !== undefined) {
          rolledBack.push({ itemId, value: previousFeedback });
        }
        return {
          ...prev,
          [sessionId]: { ...current, feedback: rolledBack },
        };
      });
      setErrors((prev) => ({ ...prev, [sessionId]: userFacingError(cause) }));
    } finally {
      setPendingFeedbackItemIds((prev) => {
        const next = new Set(prev);
        next.delete(itemId);
        return next;
      });
    }
  }, [pendingFeedbackItemIds, states]);

  const projectRevision = useCallback((
    sessionId: string,
    revision: RunRevisionResult,
  ): void => {
    setStates((prev) => {
      const current = prev[sessionId] ?? EMPTY_STATE;
      const revisions = current.revisions.filter(
        (entry) => entry.runId !== revision.run.id,
      );
      revisions.push({
        runId: revision.run.id,
        sourceRunId: revision.sourceRunId,
        kind: revision.kind,
      });
      return { ...prev, [sessionId]: { ...current, revisions } };
    });
  }, []);

  const clear = useCallback((): void => {
    loadTokenRef.current += 1;
    setLoadingSessionId(undefined);
    setPendingFeedbackItemIds(new Set());
  }, []);

  const responseState = currentSessionId
    ? (states[currentSessionId] ?? EMPTY_STATE)
    : EMPTY_STATE;

  return [
    {
      responseState,
      pendingFeedbackItemIds,
      loadingSessionId,
      error: currentSessionId ? errors[currentSessionId] : undefined,
    },
    { load, setFeedback, projectRevision, clear },
  ];
}
