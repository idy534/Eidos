import { useReducer, useCallback, useRef } from "react";
import type { ApprovalRequest } from "../contracts.js";
import { userFacingError } from "../session-state.js";

export interface ApprovalState {
  approvals: ApprovalRequest[];
  respondingApprovalIds: ReadonlySet<string>;
  respondingKindByApprovalId: Readonly<Record<string, "approve" | "reject">>;
  errorsByApprovalId: Readonly<Record<string, string>>;
  expiredApprovalIds: ReadonlySet<string>;
  feedbackDialogApproval: ApprovalRequest | null;
  feedbackDialogError: string | undefined;
}

export type ApprovalAction =
  | { type: "merge"; approvals: ApprovalRequest[] }
  | { type: "added"; approval: ApprovalRequest }
  | { type: "response_started"; approvalId: string; kind: "approve" | "reject" }
  | { type: "response_succeeded"; approvalId: string }
  | { type: "response_failed"; approvalId: string; error: string }
  | { type: "response_expired"; approvalId: string; error: string }
  | { type: "response_finished"; approvalId: string }
  | { type: "run_completed"; runId: string }
  | { type: "dialog_opened"; approval: ApprovalRequest }
  | { type: "dialog_closed" };

export const initialApprovalState: ApprovalState = {
  approvals: [],
  respondingApprovalIds: new Set<string>(),
  respondingKindByApprovalId: {},
  errorsByApprovalId: {},
  expiredApprovalIds: new Set<string>(),
  feedbackDialogApproval: null,
  feedbackDialogError: undefined,
};

export function approvalReducer(state: ApprovalState, action: ApprovalAction): ApprovalState {
  switch (action.type) {
    case "added": {
      const filtered = state.approvals.filter((a) => a.id !== action.approval.id);
      return {
        ...state,
        approvals: [...filtered, action.approval],
      };
    }
    case "merge": {
      const incomingIds = new Set(action.approvals.map((a) => a.id));
      const nextExpired = new Set(Array.from(state.expiredApprovalIds).filter((id) => incomingIds.has(id)));
      const nextErrors = { ...state.errorsByApprovalId };
      for (const id of Object.keys(nextErrors)) {
        if (!incomingIds.has(id)) {
          delete nextErrors[id];
        }
      }
      return {
        ...state,
        approvals: action.approvals,
        expiredApprovalIds: nextExpired,
        errorsByApprovalId: nextErrors,
      };
    }
    case "response_started": {
      const nextResponding = new Set(state.respondingApprovalIds);
      nextResponding.add(action.approvalId);
      return {
        ...state,
        respondingApprovalIds: nextResponding,
        respondingKindByApprovalId: {
          ...state.respondingKindByApprovalId,
          [action.approvalId]: action.kind,
        },
      };
    }
    case "response_succeeded": {
      const nextResponding = new Set(state.respondingApprovalIds);
      nextResponding.delete(action.approvalId);
      const nextRespondingKind = { ...state.respondingKindByApprovalId };
      delete nextRespondingKind[action.approvalId];
      const nextErrors = { ...state.errorsByApprovalId };
      delete nextErrors[action.approvalId];

      const closeDialog = state.feedbackDialogApproval?.id === action.approvalId;

      return {
        ...state,
        approvals: state.approvals.filter((a) => a.id !== action.approvalId),
        respondingApprovalIds: nextResponding,
        respondingKindByApprovalId: nextRespondingKind,
        errorsByApprovalId: nextErrors,
        feedbackDialogApproval: closeDialog ? null : state.feedbackDialogApproval,
        feedbackDialogError: closeDialog ? undefined : state.feedbackDialogError,
      };
    }
    case "response_failed": {
      const nextResponding = new Set(state.respondingApprovalIds);
      nextResponding.delete(action.approvalId);
      const nextRespondingKind = { ...state.respondingKindByApprovalId };
      delete nextRespondingKind[action.approvalId];

      const isDialogTarget = state.feedbackDialogApproval?.id === action.approvalId;

      return {
        ...state,
        respondingApprovalIds: nextResponding,
        respondingKindByApprovalId: nextRespondingKind,
        errorsByApprovalId: {
          ...state.errorsByApprovalId,
          [action.approvalId]: action.error,
        },
        feedbackDialogError: isDialogTarget ? action.error : state.feedbackDialogError,
      };
    }
    case "response_expired": {
      const nextResponding = new Set(state.respondingApprovalIds);
      nextResponding.delete(action.approvalId);
      const nextRespondingKind = { ...state.respondingKindByApprovalId };
      delete nextRespondingKind[action.approvalId];
      const nextExpired = new Set(state.expiredApprovalIds);
      nextExpired.add(action.approvalId);

      const isDialogTarget = state.feedbackDialogApproval?.id === action.approvalId;

      return {
        ...state,
        respondingApprovalIds: nextResponding,
        respondingKindByApprovalId: nextRespondingKind,
        expiredApprovalIds: nextExpired,
        errorsByApprovalId: {
          ...state.errorsByApprovalId,
          [action.approvalId]: action.error,
        },
        feedbackDialogApproval: isDialogTarget ? null : state.feedbackDialogApproval,
        feedbackDialogError: isDialogTarget ? undefined : state.feedbackDialogError,
      };
    }
    case "response_finished": {
      const nextResponding = new Set(state.respondingApprovalIds);
      nextResponding.delete(action.approvalId);
      const nextRespondingKind = { ...state.respondingKindByApprovalId };
      delete nextRespondingKind[action.approvalId];
      return {
        ...state,
        respondingApprovalIds: nextResponding,
        respondingKindByApprovalId: nextRespondingKind,
      };
    }
    case "run_completed": {
      const removedIds = new Set(
        state.approvals.filter((a) => a.runId === action.runId).map((a) => a.id),
      );
      if (removedIds.size === 0) return state;

      const nextResponding = new Set(state.respondingApprovalIds);
      const nextExpired = new Set(state.expiredApprovalIds);
      const nextRespondingKind = { ...state.respondingKindByApprovalId };
      const nextErrors = { ...state.errorsByApprovalId };

      for (const id of removedIds) {
        nextResponding.delete(id);
        nextExpired.delete(id);
        delete nextRespondingKind[id];
        delete nextErrors[id];
      }

      const closeDialog = Boolean(
        state.feedbackDialogApproval && removedIds.has(state.feedbackDialogApproval.id),
      );

      return {
        ...state,
        approvals: state.approvals.filter((a) => a.runId !== action.runId),
        respondingApprovalIds: nextResponding,
        respondingKindByApprovalId: nextRespondingKind,
        expiredApprovalIds: nextExpired,
        errorsByApprovalId: nextErrors,
        feedbackDialogApproval: closeDialog ? null : state.feedbackDialogApproval,
        feedbackDialogError: closeDialog ? undefined : state.feedbackDialogError,
      };
    }
    case "dialog_opened": {
      return {
        ...state,
        feedbackDialogApproval: action.approval,
        feedbackDialogError: undefined,
      };
    }
    case "dialog_closed": {
      return {
        ...state,
        feedbackDialogApproval: null,
        feedbackDialogError: undefined,
      };
    }
    default:
      return state;
  }
}

export interface ApprovalControllerState {
  approvals: ApprovalRequest[];
  respondingApprovalId: string | undefined;
  respondingApprovalIds: ReadonlySet<string>;
  expiredApprovalIds: ReadonlySet<string>;
  errorsByApprovalId: Readonly<Record<string, string>>;
  feedbackDialogApproval: ApprovalRequest | null;
  feedbackDialogError: string | undefined;
}

export interface ApprovalControllerActions {
  addApproval: (request: ApprovalRequest) => void;
  mergeApprovals: (incoming: ApprovalRequest[]) => void;
  clearApprovalsForRun: (runId: string) => void;
  approve: (request: ApprovalRequest) => Promise<void>;
  openRejectDialog: (request: ApprovalRequest) => void;
  closeFeedbackDialog: () => void;
  submitReject: (request: ApprovalRequest, feedback: string) => Promise<void>;
}

export function useApprovalController(): [ApprovalControllerState, ApprovalControllerActions] {
  const [state, dispatch] = useReducer(approvalReducer, initialApprovalState);
  const activeLockRef = useRef<Set<string>>(new Set());

  const addApproval = useCallback((request: ApprovalRequest): void => {
    dispatch({ type: "added", approval: request });
  }, []);

  const mergeApprovals = useCallback((incoming: ApprovalRequest[]): void => {
    dispatch({ type: "merge", approvals: incoming });
  }, []);

  const clearApprovalsForRun = useCallback((runId: string): void => {
    dispatch({ type: "run_completed", runId });
  }, []);

  const approve = useCallback(async (request: ApprovalRequest): Promise<void> => {
    if (activeLockRef.current.has(request.id) || state.expiredApprovalIds.has(request.id)) {
      return;
    }
    activeLockRef.current.add(request.id);
    dispatch({ type: "response_started", approvalId: request.id, kind: "approve" });

    try {
      const accepted = await window.eidosRuntime.respondApproval(request.id, "approve");
      if (!accepted) {
        dispatch({
          type: "response_expired",
          approvalId: request.id,
          error: "该审批已过期或已被处理。",
        });
      } else {
        dispatch({ type: "response_succeeded", approvalId: request.id });
      }
    } catch (cause) {
      dispatch({
        type: "response_failed",
        approvalId: request.id,
        error: userFacingError(cause),
      });
    } finally {
      activeLockRef.current.delete(request.id);
    }
  }, [state.expiredApprovalIds]);

  const openRejectDialog = useCallback((request: ApprovalRequest): void => {
    if (state.expiredApprovalIds.has(request.id)) return;
    dispatch({ type: "dialog_opened", approval: request });
  }, [state.expiredApprovalIds]);

  const closeFeedbackDialog = useCallback((): void => {
    dispatch({ type: "dialog_closed" });
  }, []);

  const submitReject = useCallback(async (
    request: ApprovalRequest,
    feedback: string,
  ): Promise<void> => {
    if (activeLockRef.current.has(request.id) || state.expiredApprovalIds.has(request.id)) {
      return;
    }
    activeLockRef.current.add(request.id);
    dispatch({ type: "response_started", approvalId: request.id, kind: "reject" });

    try {
      const trimmedFeedback = feedback.trim();
      const accepted = await window.eidosRuntime.respondApproval(
        request.id,
        "reject",
        trimmedFeedback.length > 0 ? trimmedFeedback : undefined,
      );
      if (!accepted) {
        dispatch({
          type: "response_expired",
          approvalId: request.id,
          error: "该审批已过期或已被处理。",
        });
      } else {
        dispatch({ type: "response_succeeded", approvalId: request.id });
      }
    } catch (cause) {
      dispatch({
        type: "response_failed",
        approvalId: request.id,
        error: userFacingError(cause),
      });
    } finally {
      activeLockRef.current.delete(request.id);
    }
  }, [state.expiredApprovalIds]);

  const controllerState: ApprovalControllerState = {
    approvals: state.approvals,
    respondingApprovalId: Array.from(state.respondingApprovalIds)[0],
    respondingApprovalIds: state.respondingApprovalIds,
    expiredApprovalIds: state.expiredApprovalIds,
    errorsByApprovalId: state.errorsByApprovalId,
    feedbackDialogApproval: state.feedbackDialogApproval,
    feedbackDialogError: state.feedbackDialogError,
  };

  const actions: ApprovalControllerActions = {
    addApproval,
    mergeApprovals,
    clearApprovalsForRun,
    approve,
    openRejectDialog,
    closeFeedbackDialog,
    submitReject,
  };

  return [controllerState, actions];
}
