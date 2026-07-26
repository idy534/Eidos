import { useState, useCallback } from "react";
import type { ApprovalRequest } from "../contracts.js";
import { userFacingError } from "../session-state.js";


export interface ApprovalControllerState {
  approvals: ApprovalRequest[];
  /** ID of approval currently being processed, for per-card loading */
  respondingApprovalId: string | undefined;
  /** Approval for which the feedback dialog is open */
  feedbackDialogApproval: ApprovalRequest | null;
  /** Error for the active feedback dialog submission */
  feedbackDialogError: string | undefined;
}

export interface ApprovalControllerActions {
  /** Called when a new approval arrives via IPC push */
  addApproval: (request: ApprovalRequest) => void;
  /** Called when approvals are loaded on session ready */
  mergeApprovals: (incoming: ApprovalRequest[]) => void;
  /** Remove approvals associated with a completed run */
  clearApprovalsForRun: (runId: string) => void;
  /** Approve immediately (no feedback) */
  approve: (request: ApprovalRequest) => Promise<void>;
  /** Open the feedback dialog for this rejection */
  openRejectDialog: (request: ApprovalRequest) => void;
  /** Close without submitting */
  closeFeedbackDialog: () => void;
  /** Submit rejection with optional feedback */
  submitReject: (request: ApprovalRequest, feedback: string) => Promise<void>;
}

/**
 * Manages pending approvals.
 *
 * Correctness invariants:
 * - Approving does NOT carry feedback.
 * - Per-approval pending state — only the responding card is locked.
 * - Expired approvals show a local error, not a global banner.
 * - On successful response, the approval is removed from the list.
 */
export function useApprovalController(): [ApprovalControllerState, ApprovalControllerActions] {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [respondingApprovalId, setRespondingApprovalId] = useState<string | undefined>(undefined);
  const [feedbackDialogApproval, setFeedbackDialogApproval] = useState<ApprovalRequest | null>(null);
  const [feedbackDialogError, setFeedbackDialogError] = useState<string | undefined>(undefined);

  const addApproval = useCallback((request: ApprovalRequest): void => {
    setApprovals((prev) => [...prev.filter((item) => item.id !== request.id), request]);
  }, []);

  const mergeApprovals = useCallback((incoming: ApprovalRequest[]): void => {
    setApprovals((current) => incoming.reduce(
      (merged, approval) => [
        ...merged.filter((item) => item.id !== approval.id),
        approval,
      ],
      current,
    ));
  }, []);

  const clearApprovalsForRun = useCallback((runId: string): void => {
    setApprovals((prev) => prev.filter((a) => a.runId !== runId));
  }, []);

  const approve = useCallback(async (request: ApprovalRequest): Promise<void> => {
    if (respondingApprovalId) return;
    setRespondingApprovalId(request.id);
    try {
      // Approve MUST NOT carry feedback
      const accepted = await window.eidosRuntime.respondApproval(request.id, "approve");
      if (!accepted) {
        // Approval expired — show inline, not global
        console.warn("[approval] Approval already resolved:", request.id);
        return;
      }
      setApprovals((prev) => prev.filter((a) => a.id !== request.id));
    } catch (cause) {
      // Inline error: approval card shows it
      console.error("[approval] Approve failed:", userFacingError(cause));
    } finally {
      setRespondingApprovalId(undefined);
    }
  }, [respondingApprovalId]);

  const openRejectDialog = useCallback((request: ApprovalRequest): void => {
    setFeedbackDialogError(undefined);
    setFeedbackDialogApproval(request);
  }, []);

  const closeFeedbackDialog = useCallback((): void => {
    setFeedbackDialogApproval(null);
    setFeedbackDialogError(undefined);
  }, []);

  const submitReject = useCallback(async (
    request: ApprovalRequest,
    feedback: string,
  ): Promise<void> => {
    if (respondingApprovalId) return;
    setRespondingApprovalId(request.id);
    setFeedbackDialogError(undefined);
    try {
      const trimmedFeedback = feedback.trim();
      const accepted = await window.eidosRuntime.respondApproval(
        request.id,
        "reject",
        trimmedFeedback.length > 0 ? trimmedFeedback : undefined,
      );
      if (!accepted) {
        setFeedbackDialogError("这个审批已经失效，无需再次处理。");
        return;
      }
      setApprovals((prev) => prev.filter((a) => a.id !== request.id));
      setFeedbackDialogApproval(null);
    } catch (cause) {
      setFeedbackDialogError(userFacingError(cause));
    } finally {
      setRespondingApprovalId(undefined);
    }
  }, [respondingApprovalId]);

  const state: ApprovalControllerState = {
    approvals,
    respondingApprovalId,
    feedbackDialogApproval,
    feedbackDialogError,
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

  return [state, actions];
}
