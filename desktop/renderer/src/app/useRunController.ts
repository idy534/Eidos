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
  kind: "start" | "continue";
  runId?: string;
}

export interface RunControllerState {
  composerMode: ComposerMode;
  activeRun: Run | undefined;
  input: string;
  inputs: Record<string, string>;
  isSubmitting: boolean;
  submitKind: "start" | "continue" | undefined;
  cancelingRunId: string | undefined;
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
  cancelRun: (runId: string) => Promise<void>;
  clearError: () => void;
}

/**
 * Manages the active Run lifecycle, atomic submission locks, and Session-scoped input state.
 *
 * Correctness Invariants Enforced:
 * 1. Synchronous Ref-based submission lock prevents concurrent IPC calls before state updates.
 * 2. Inputs are Session-scoped (Record<sessionId, string>); switching sessions preserves drafts.
 * 3. Returned Run objects are projected immediately upon IPC resolution.
 * 4. Stale responses (wrong sessionId) do not mutate other session inputs or states.
 * 5. waiting_user_input strictly calls continueRun; idle strictly calls startRun.
 * 6. All locks are released in `finally` blocks.
 */
export function useRunController(
  snapshot: SessionSnapshot | undefined,
  isStorageReady: boolean,
): [RunControllerState, RunControllerActions] {
  // Session-scoped draft inputs
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [isStarting, setIsStarting] = useState(false);
  const [submitKind, setSubmitKind] = useState<"start" | "continue" | undefined>(undefined);
  const [cancelingRunId, setCancelingRunId] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  // Synchronous lock ref to prevent race conditions during rapid shortcut/click dispatch
  const submissionLockRef = useRef<SubmissionOperation | undefined>(undefined);

  const currentSessionId = snapshot?.session.id;
  const input = currentSessionId ? (inputs[currentSessionId] ?? "") : "";

  const activeRun = snapshot ? findActiveRun(snapshot.runs) : undefined;
  const composerMode = deriveComposerMode(isStorageReady, activeRun, isStarting);

  const setInputForSession = useCallback((sessionId: string, value: string): void => {
    setInputs((prev) => ({
      ...prev,
      [sessionId]: value,
    }));
  }, []);

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
    // 1. Synchronous lock check
    if (submissionLockRef.current) {
      return;
    }

    const sessionId = currentSnapshot.session.id;
    const sessionInput = inputs[sessionId] ?? "";

    // Defensive guards
    if (!storageReady) return;
    if (!sessionInput.trim()) return;

    const currentActiveRun = findActiveRun(currentSnapshot.runs);
    const mode = deriveComposerMode(storageReady, currentActiveRun, isStarting);

    // Only "idle" and "waiting_user_input" allow submission
    if (mode !== "idle" && mode !== "waiting_user_input") return;

    if (mode === "waiting_user_input") {
      // continueRun path
      if (!currentActiveRun?.allowedActions?.includes("continue")) return;

      const operation: SubmissionOperation = {
        token: Symbol("run-submission"),
        sessionId,
        kind: "continue",
        runId: currentActiveRun.id,
      };

      // Synchronously acquire lock before async operations
      submissionLockRef.current = operation;
      setIsStarting(true);
      setSubmitKind("continue");
      setError(undefined);

      try {
        const returnedRun = await window.eidosRuntime.continueRun(currentActiveRun.id, sessionInput.trim());

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
          setError(userFacingError(cause));
        }
      } finally {
        if (submissionLockRef.current?.token === operation.token) {
          submissionLockRef.current = undefined;
          setIsStarting(false);
          setSubmitKind(undefined);
        }
      }
    } else if (mode === "idle") {
      // startRun path — double check no active run exists
      const freshActiveRun = findActiveRun(currentSnapshot.runs);
      if (freshActiveRun) return;

      const operation: SubmissionOperation = {
        token: Symbol("run-submission"),
        sessionId,
        kind: "start",
      };

      // Synchronously acquire lock before async operations
      submissionLockRef.current = operation;
      setIsStarting(true);
      setSubmitKind("start");
      setError(undefined);

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
          setError(userFacingError(cause));
        }
      } finally {
        if (submissionLockRef.current?.token === operation.token) {
          submissionLockRef.current = undefined;
          setIsStarting(false);
          setSubmitKind(undefined);
        }
      }
    }
  }, [inputs, isStarting]);

  const cancelingRunIdRef = useRef<string | undefined>(undefined);

  const cancelRun = useCallback(async (runId: string): Promise<void> => {
    if (cancelingRunIdRef.current) return;
    cancelingRunIdRef.current = runId;
    setCancelingRunId(runId);
    try {
      await window.eidosRuntime.cancelRun(runId);
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      cancelingRunIdRef.current = undefined;
      setCancelingRunId(undefined);
    }
  }, []);

  const state: RunControllerState = {
    composerMode,
    activeRun,
    input,
    inputs,
    isSubmitting: isStarting,
    submitKind,
    cancelingRunId,
    error,
  };

  const actions: RunControllerActions = {
    setInput,
    setInputForSession,
    submitInput,
    cancelRun,
    clearError: () => setError(undefined),
  };

  return [state, actions];
}
