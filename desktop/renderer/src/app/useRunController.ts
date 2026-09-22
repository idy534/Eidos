import type { RunPlanningOptions } from "../../../shared/planning.generated.js";
import type { InputReference } from "../../../shared/input-context.js";
import { useInputDrafts } from "./useInputDrafts.js";
import { useCallback, useRef, useState } from "react";
import type { ApprovalMode, ModelId, ModelReasoningSelection, Run, RunRevisionResult, SessionSnapshot } from "../contracts.js";
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
  references: InputReference[];
  draftReady: boolean;
  isSubmitting: boolean;
  submitKind: "start" | undefined;
  cancelingRunId: string | undefined;
  errorsBySessionId: Readonly<Record<string, string>>;
  error: string | undefined;
}

export interface RunControllerActions {
  addReference: (sessionId: string, reference: InputReference) => void;
  removeReference: (id: string) => void;
  clearDraftIfUnchanged: (sessionId: string, text: string, references: InputReference[]) => void;
  setInput: (value: string) => void;
  setInputForSession: (sessionId: string, value: string) => void;
  submitInput: (params: {
    snapshot: SessionSnapshot;
    selectedModelId: ModelId;
    reasoningSelection?: ModelReasoningSelection | undefined;
    approvalMode?: ApprovalMode | undefined;
    planning?: RunPlanningOptions | undefined;
    isStorageReady: boolean;
    inputOverride?: string;
    referencesOverride?: InputReference[];
    onRunProjected?: (sessionId: string, run: Run) => void;
  }) => Promise<boolean>;
  reviseRun: (params: {
    snapshot: SessionSnapshot;
    sourceRunId: string;
    userInput?: string;
    references?: string[];
    isStorageReady: boolean;
    onRunProjected?: (sessionId: string, run: Run) => void;
    onRevisionProjected?: (revision: RunRevisionResult) => void;
    onRefreshSession?: (sessionId: string) => Promise<unknown>;
  }) => Promise<boolean>;
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
 * 6. Start and revision operations share the same synchronous lock.
 * 7. All locks are released in `finally` blocks.
 */
export function useRunController(
  snapshot: SessionSnapshot | undefined,
  isStorageReady: boolean,
): [RunControllerState, RunControllerActions] {
  const draftStore = useInputDrafts(snapshot?.session.id, isStorageReady);
  const inputs = Object.fromEntries(Object.entries(draftStore.drafts).map(([id, draft]) => [id, draft.text]));
  const [submissionOperation, setSubmissionOperation] = useState<SubmissionOperation | undefined>(undefined);
  const [cancelingRunId, setCancelingRunId] = useState<string | undefined>(undefined);
  const [errorsBySessionId, setErrorsBySessionId] = useState<Record<string, string>>({});

  const submissionLockRef = useRef<SubmissionOperation | undefined>(undefined);

  const currentSessionId = snapshot?.session.id;
  const input = currentSessionId ? (inputs[currentSessionId] ?? "") : "";
  const error = (currentSessionId ? errorsBySessionId[currentSessionId] : undefined) ?? draftStore.error;
  const references = currentSessionId ? draftStore.drafts[currentSessionId]?.references ?? [] : [];

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
    draftStore.update(sessionId, (draft) => ({ ...draft, text: value }));
    clearSessionError(sessionId);
  }, [clearSessionError, draftStore.update]);

  const clearDraftIfUnchanged = useCallback((sessionId: string, text: string, references: InputReference[]) => {
    draftStore.update(sessionId, (draft) => draft.text === text && JSON.stringify(draft.references) === JSON.stringify(references) ? { text: "", references: [] } : draft);
  }, [draftStore.update]);

  const setInput = useCallback((value: string): void => {
    if (!currentSessionId) return;
    setInputForSession(currentSessionId, value);
  }, [currentSessionId, setInputForSession]);

  const submitInput = useCallback(async ({
    snapshot: currentSnapshot,
    selectedModelId,
    reasoningSelection,
    approvalMode,
    planning,
    isStorageReady: storageReady,
    inputOverride,
    referencesOverride,
    onRunProjected,
  }: {
    snapshot: SessionSnapshot;
    selectedModelId: ModelId;
    reasoningSelection?: ModelReasoningSelection | undefined;
    approvalMode?: ApprovalMode | undefined;
    planning?: RunPlanningOptions | undefined;
    isStorageReady: boolean;
    inputOverride?: string;
    referencesOverride?: InputReference[];
    onRunProjected?: (sessionId: string, run: Run) => void;
  }): Promise<boolean> => {
    const sessionId = currentSnapshot.session.id;
    const sessionInput = inputOverride ?? draftStore.values.current[sessionId]?.text ?? "";
    const submittedReferences = referencesOverride ?? (inputOverride === undefined ? draftStore.values.current[sessionId]?.references ?? [] : []);

    if (submissionLockRef.current) {
      if (submissionLockRef.current.sessionId !== sessionId) {
        setErrorsBySessionId((prev) => ({
          ...prev,
          [sessionId]: "另一个任务正在启动，请稍后重试。",
        }));
      }
      return false;
    }

    if (!storageReady || (inputOverride === undefined && !draftStore.ready)) return false;
    if (!sessionInput.trim() && !submittedReferences.length) return false;

    const currentActiveRun = findActiveRun(currentSnapshot.runs);
    const mode = deriveComposerMode(storageReady, currentActiveRun, false);
    if (mode !== "idle") return false;

    const freshActiveRun = findActiveRun(currentSnapshot.runs);
    if (freshActiveRun) return false;

    const token = Symbol("run-submission");
    const operation: SubmissionOperation = {
      token,
      sessionId,
      kind: "start",
    };

    submissionLockRef.current = operation;
    setSubmissionOperation(operation);
    clearSessionError(sessionId);

    try {
      if (inputOverride === undefined) await draftStore.flush(sessionId);
      const returnedRun = await window.eidosRuntime.startRun(
        sessionId, /^\/plan(?:\s|$)/.test(sessionInput.trim()) ? sessionInput.trim().replace(/^\/plan(?:\s+|$)/, "").trim() || "请先制定计划。" : sessionInput.trim(), selectedModelId, reasoningSelection, approvalMode, submittedReferences.map((value) => value.id),
        /^\/plan(?:\s|$)/.test(sessionInput.trim()) ? { ...planning, workMode: "plan" } : planning,
      );

      if (returnedRun.sessionId === sessionId) {
        onRunProjected?.(sessionId, returnedRun);
        if (inputOverride === undefined) {
          clearDraftIfUnchanged(sessionId, sessionInput, submittedReferences);
          void draftStore.flush(sessionId).catch(() => {});
        }
        return true;
      }
      return false;
    } catch (cause) {
      if (submissionLockRef.current?.token === operation.token) {
        const errMsg = userFacingError(cause);
        setErrorsBySessionId((prev) => ({
          ...prev,
          [sessionId]: errMsg,
        }));
      }
      return false;
    } finally {
      if (submissionLockRef.current?.token === operation.token) {
        submissionLockRef.current = undefined;
        setSubmissionOperation(undefined);
      }
    }
  }, [draftStore.values, draftStore.flush, draftStore.ready, clearDraftIfUnchanged, clearSessionError]);

  const reviseRun = useCallback(async ({
    snapshot: currentSnapshot,
    sourceRunId,
    userInput,
    references,
    isStorageReady: storageReady,
    onRunProjected,
    onRevisionProjected,
    onRefreshSession,
  }: {
    snapshot: SessionSnapshot;
    sourceRunId: string;
    userInput?: string;
    references?: string[];
    isStorageReady: boolean;
    onRunProjected?: (sessionId: string, run: Run) => void;
    onRevisionProjected?: (revision: RunRevisionResult) => void;
    onRefreshSession?: (sessionId: string) => Promise<unknown>;
  }): Promise<boolean> => {
    const sessionId = currentSnapshot.session.id;
    if (submissionLockRef.current || !storageReady || findActiveRun(currentSnapshot.runs)) {
      return false;
    }
    if (userInput !== undefined && !userInput.trim() && !references?.length) return false;

    const token = Symbol("run-revision");
    const operation: SubmissionOperation = {
      token,
      sessionId,
      kind: "start",
    };
    submissionLockRef.current = operation;
    setSubmissionOperation(operation);
    clearSessionError(sessionId);

    try {
      const revision = await window.eidosRuntime.reviseRun(
        sourceRunId,
        userInput?.trim(),
        references,
      );
      if (revision.run.sessionId !== sessionId) return false;
      onRevisionProjected?.(revision);
      onRunProjected?.(sessionId, revision.run);
      try {
        await onRefreshSession?.(sessionId);
      } catch (cause) {
        setErrorsBySessionId((previous) => ({ ...previous, [sessionId]: userFacingError(cause) }));
      }
      return true;
    } catch (cause) {
      if (submissionLockRef.current?.token === operation.token) {
        setErrorsBySessionId((prev) => ({
          ...prev,
          [sessionId]: userFacingError(cause),
        }));
      }
      return false;
    } finally {
      if (submissionLockRef.current?.token === operation.token) {
        submissionLockRef.current = undefined;
        setSubmissionOperation(undefined);
      }
    }
  }, [clearSessionError]);

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
    references,
    draftReady: draftStore.ready,
    isSubmitting,
    submitKind,
    cancelingRunId: cancelingRunId === activeRun?.id ? cancelingRunId : undefined,
    errorsBySessionId,
    error,
  };

  const actions: RunControllerActions = {
    addReference: draftStore.add,
    removeReference: (id) => {
      if (currentSessionId) draftStore.update(currentSessionId, (draft) => ({ ...draft, references: draft.references.filter((value) => value.id !== id) }));
    },
    clearDraftIfUnchanged,
    setInput,
    setInputForSession,
    submitInput,
    reviseRun,
    cancelRun,
    clearError: clearErrorAction,
  };

  return [state, actions];
}
