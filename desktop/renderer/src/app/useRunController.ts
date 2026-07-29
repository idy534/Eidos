import { useCallback, useRef, useState } from "react";
import type { ModelId, Run, SessionSnapshot } from "../contracts.js";
import {
  deriveComposerMode,
  findActiveRun,
  type ComposerMode,
  userFacingError,
} from "../session-state.js";

export interface SubmissionOperation {
  token: symbol;
  sessionId: string;
  kind: "start";
}

export interface RunControllerState {
  composerMode: ComposerMode;
  activeRun: Run | undefined;
  input: string;
  inputs: Record<string, string>;
  isSubmitting: boolean;
  submitKind: "start" | undefined;
  cancelingRunId: string | undefined;
  errorsBySessionId: Readonly<Record<string, string>>;
  error: string | undefined;
}

export interface RunControllerActions {
  setInput: (value: string) => void;
  setInputForSession: (sessionId: string, value: string) => void;
  submitInput: (params: {
    snapshot: SessionSnapshot;
    selectedModelId: ModelId;
    isStorageReady: boolean;
    onRunProjected?: (sessionId: string, run: Run) => void;
  }) => Promise<void>;
  cancelRun: (params: { runId: string; sessionId: string } | string) => Promise<void>;
  clearError: (sessionId?: string) => void;
}

/**
 * Manages the active Run lifecycle, atomic submission locks, and Session-scoped input state.
 *
 * Correctness Invariants Enforced:
 * 1. Synchronous Ref-based submission lock prevents concurrent IPC calls before state updates.
 * 2. Inputs are Session-scoped (Record<sessionId, string>); switching sessions preserves drafts.
 * 3. Returned Run objects are projected immediately upon IPC resolution.
 * 4. Stale responses (wrong sessionId) do not mutate other session inputs or states.
 * 5. Errors are Session-scoped (Record<sessionId, string>).
 * 6. All locks are released in `finally` blocks.
 */
export function useRunController(
  snapshot: SessionSnapshot | undefined,
  isStorageReady: boolean,
): [RunControllerState, RunControllerActions] {
  // Session-scoped draft inputs
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [submissionOperation, setSubmissionOperation] = useState<SubmissionOperation | undefined>(undefined);
  const [cancelingRunId, setCancelingRunId] = useState<string | undefined>(undefined);
  const [errorsBySessionId, setErrorsBySessionId] = useState<Record<string, string>>({});

  // Synchronous lock ref to prevent race conditions during rapid shortcut/click dispatch
  const submissionLockRef = useRef<SubmissionOperation | undefined>(undefined);

  const currentSessionId = snapshot?.session.id;
  const input = currentSessionId ? (inputs[currentSessionId] ?? "") : "";
  const error = currentSessionId ? errorsBySessionId[currentSessionId] : undefined;

  const currentSubmission = submissionOperation?.sessionId === currentSessionId
    ? submissionOperation
    : undefined;
  const isSubmitting = currentSubmission !== undefined;
  const submitKind = currentSubmission?.kind;

  const activeRun = snapshot ? findActiveRun(snapshot.runs) : undefined;
  const composerMode = deriveComposerMode(isStorageReady, activeRun, isSubmitting);

  const clearSessionError = useCallback((sessionId: string): void => {
    setErrorsBySessionId((prev) => {
      if (!prev[sessionId]) return prev;
      const next = { ...prev };
      delete next[sessionId];
      return next;
    });
  }, []);

  const setInputForSession = useCallback((sessionId: string, value: string): void => {
    setInputs((prev) => ({
      ...prev,
      [sessionId]: value,
    }));
    clearSessionError(sessionId);
  }, [clearSessionError]);

  const setInput = useCallback((value: string): void => {
    if (!currentSessionId) return;
    setInputForSession(currentSessionId, value);
  }, [currentSessionId, setInputForSession]);

  const submitInput = useCallback(async ({
    snapshot: currentSnapshot,
    selectedModelId,
    isStorageReady: storageReady,
    onRunProjected,
  }: {
    snapshot: SessionSnapshot;
    selectedModelId: ModelId;
    isStorageReady: boolean;
    onRunProjected?: (sessionId: string, run: Run) => void;
  }): Promise<void> => {
    const sessionId = currentSnapshot.session.id;
    const sessionInput = inputs[sessionId] ?? "";

    // Synchronous lock check — if another session owns the lock, present explicit local busy feedback
    if (submissionLockRef.current) {
      if (submissionLockRef.current.sessionId !== sessionId) {
        setErrorsBySessionId((prev) => ({
          ...prev,
          [sessionId]: "另一个任务正在启动，请稍后重试。",
        }));
      }
      return;
    }

    // Defensive guards
    if (!storageReady) return;
    if (!sessionInput.trim()) return;

    const currentActiveRun = findActiveRun(currentSnapshot.runs);
    // Evaluate eligibility using target session's state
    const mode = deriveComposerMode(storageReady, currentActiveRun, false);

    if (mode !== "idle") return;

    // Double check no active run exists before starting.
    const freshActiveRun = findActiveRun(currentSnapshot.runs);
    if (freshActiveRun) return;

    const token = Symbol("run-submission");
    const operation: SubmissionOperation = {
      token,
      sessionId,
      kind: "start",
    };

    // Synchronously acquire lock before async operations
    submissionLockRef.current = operation;
    setSubmissionOperation(operation);
    clearSessionError(sessionId);

    try {
      const returnedRun = await window.eidosRuntime.startRun(sessionId, sessionInput.trim(), selectedModelId);

      // Verify response is still for the submitted session
      if (returnedRun.sessionId === sessionId) {
        // Immediately project returned run
        onRunProjected?.(sessionId, returnedRun);
        // Clear input ONLY for the target session
        setInputs((prev) => {
          const next = { ...prev };
          delete next[sessionId];
          return next;
        });
      }
    } catch (cause) {
      if (submissionLockRef.current?.token === operation.token) {
        const errMsg = userFacingError(cause);
        setErrorsBySessionId((prev) => ({
          ...prev,
          [sessionId]: errMsg,
        }));
      }
    } finally {
      if (submissionLockRef.current?.token === operation.token) {
        submissionLockRef.current = undefined;
        setSubmissionOperation(undefined);
      }
    }
  }, [inputs, clearSessionError]);

  const cancelingRunIdRef = useRef<string | undefined>(undefined);

  const cancelRun = useCallback(async (params: { runId: string; sessionId: string } | string): Promise<void> => {
    const runId = typeof params === "string" ? params : params.runId;
    const targetSessionId = typeof params === "string" ? (currentSessionId ?? "") : params.sessionId;

    if (cancelingRunIdRef.current) return;
    cancelingRunIdRef.current = runId;
    setCancelingRunId(runId);
    try {
      await window.eidosRuntime.cancelRun(runId);
    } catch (cause) {
      if (targetSessionId) {
        setErrorsBySessionId((prev) => ({
          ...prev,
          [targetSessionId]: userFacingError(cause),
        }));
      }
    } finally {
      cancelingRunIdRef.current = undefined;
      setCancelingRunId(undefined);
    }
  }, [currentSessionId]);

  const clearErrorAction = useCallback((sessionId?: string): void => {
    const sid = sessionId ?? currentSessionId;
    if (sid) {
      clearSessionError(sid);
    }
  }, [currentSessionId, clearSessionError]);

  const state: RunControllerState = {
    composerMode,
    activeRun,
    input,
    inputs,
    isSubmitting,
    submitKind,
    cancelingRunId: cancelingRunId === activeRun?.id ? cancelingRunId : undefined,
    errorsBySessionId,
    error,
  };

  const actions: RunControllerActions = {
    setInput,
    setInputForSession,
    submitInput,
    cancelRun,
    clearError: clearErrorAction,
  };

  return [state, actions];
}
