import { useCallback, useRef, useState } from "react";
import type { ModelId, Run, SessionSnapshot } from "../contracts.js";
import {
  deriveComposerMode,
  findActiveRun,
  type ComposerMode,
  userFacingError,
} from "../session-state.js";


export interface RunControllerState {
  composerMode: ComposerMode;
  activeRun: Run | undefined;
  input: string;
  /** ID being canceled, for per-run loading */
  cancelingRunId: string | undefined;
  error: string | undefined;
}

export interface RunControllerActions {
  setInput: (value: string) => void;
  submitInput: (params: {
    snapshot: SessionSnapshot;
    selectedModelId: ModelId;
    isStorageReady: boolean;
  }) => Promise<void>;
  cancelRun: (runId: string) => Promise<void>;
  clearError: () => void;
}

/**
 * Manages the active Run lifecycle and Composer state machine.
 *
 * Key correctness invariants enforced here:
 * 1. Only one startRun per session at a time (isStarting guard + check snapshot).
 * 2. Stale async responses are ignored via sessionId comparison.
 * 3. waiting_user_input → continueRun; any other non-idle → block.
 * 4. Cmd+Enter and rapid clicks cannot bypass the starting guard.
 */
export function useRunController(
  snapshot: SessionSnapshot | undefined,
  isStorageReady: boolean,
): [RunControllerState, RunControllerActions] {
  const [input, setInput] = useState("");
  const [isStarting, setIsStarting] = useState(false);
  const [cancelingRunId, setCancelingRunId] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | undefined>(undefined);

  // Track which session a pending start is for — prevents stale async response
  const startingForSessionRef = useRef<string | undefined>(undefined);

  const activeRun = snapshot ? findActiveRun(snapshot.runs) : undefined;
  const composerMode = deriveComposerMode(isStorageReady, activeRun, isStarting);

  const submitInput = useCallback(async ({
    snapshot: currentSnapshot,
    selectedModelId,
    isStorageReady: storageReady,
  }: {
    snapshot: SessionSnapshot;
    selectedModelId: ModelId;
    isStorageReady: boolean;
  }): Promise<void> => {
    // Defensive guards — all must pass
    if (!storageReady) return;
    if (!input.trim()) return;

    const currentActiveRun = findActiveRun(currentSnapshot.runs);
    const mode = deriveComposerMode(storageReady, currentActiveRun, isStarting);

    // Only these two modes allow submission
    if (mode !== "idle" && mode !== "waiting_user_input") return;

    // Prevent concurrent starts
    if (isStarting) return;

    const sessionId = currentSnapshot.session.id;

    if (mode === "waiting_user_input" && currentActiveRun?.allowedActions?.includes("continue")) {
      // continueRun path
      setIsStarting(true);
      startingForSessionRef.current = sessionId;
      setError(undefined);
      try {
        const run = await window.eidosRuntime.continueRun(currentActiveRun.id, input.trim());
        // Verify the response is still for the current session
        if (startingForSessionRef.current === sessionId && run.sessionId === sessionId) {
          setInput("");
        }
      } catch (cause) {
        if (startingForSessionRef.current === sessionId) {
          setError(userFacingError(cause));
        }
      } finally {
        if (startingForSessionRef.current === sessionId) {
          setIsStarting(false);
        }
      }
    } else if (mode === "idle") {
      // startRun path — double-check no active run exists
      const freshActiveRun = findActiveRun(currentSnapshot.runs);
      if (freshActiveRun) return; // Race condition: run appeared after mode check

      setIsStarting(true);
      startingForSessionRef.current = sessionId;
      setError(undefined);
      try {
        await window.eidosRuntime.startRun(sessionId, input.trim(), selectedModelId);
        if (startingForSessionRef.current === sessionId) {
          setInput("");
        }
      } catch (cause) {
        if (startingForSessionRef.current === sessionId) {
          setError(userFacingError(cause));
        }
      } finally {
        if (startingForSessionRef.current === sessionId) {
          setIsStarting(false);
        }
      }
    }
  }, [input, isStarting]);

  const cancelRun = useCallback(async (runId: string): Promise<void> => {
    if (cancelingRunId) return; // already canceling
    setCancelingRunId(runId);
    try {
      await window.eidosRuntime.cancelRun(runId);
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setCancelingRunId(undefined);
    }
  }, [cancelingRunId]);

  const state: RunControllerState = {
    composerMode,
    activeRun,
    input,
    cancelingRunId,
    error,
  };

  const actions: RunControllerActions = {
    setInput,
    submitInput,
    cancelRun,
    clearError: () => setError(undefined),
  };

  return [state, actions];
}
