import { useState, useCallback, useRef } from "react";
import type { ApprovalRequest } from "../contracts.js";
import { userFacingError } from "../session-state.js";
import { MAX_APPROVAL_FEEDBACK_BYTES } from "../../../shared/constants.js";


export interface ApprovalControllerState {
  approvals: ApprovalRequest[];
  /** Set of approval IDs currently being processed */
  respondingApprovalIds: ReadonlySet<string>;
  /** Map of approval ID to action kind ("approve" | "reject") */
  respondingKindByApprovalId: Readonly<Record<string, "approve" | "reject">>;
  /** Approval for which the feedback dialog is open */
  feedbackDialogApproval: ApprovalRequest | null;
  /** Error for the active feedback dialog submission */
  feedbackDialogError: string | undefined;
  /** Errors keyed by approval ID for inline card display */
  errorsByApprovalId: Readonly<Record<string, string>>;
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
  setApprovalError: (approvalId: string, message: string) => void;
  clearApprovalError: (approvalId: string) => void;
}

/**
 * Manages pending approvals.
 *
 * Correctness invariants:
 * - Approving does NOT carry feedback.
 * - Per-approval pending state — Approval A pending does not block Approval B.
 * - Synchronous duplicate submission protection via useRef.
 * - Expired approvals show a local error, not a global banner.
 * - On successful response, the approval is removed from the list.
 */
export function useApprovalController(): [ApprovalControllerState, ApprovalControllerActions] {
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [respondingApprovalIds, setRespondingApprovalIds] = useState<ReadonlySet<string>>(new Set());
  const [respondingKindByApprovalId, setRespondingKindByApprovalId] = useState<Readonly<Record<string, "approve" | "reject">>>({});
  const [feedbackDialogApproval, setFeedbackDialogApproval] = useState<ApprovalRequest | null>(null);
  const [feedbackDialogError, setFeedbackDialogError] = useState<string | undefined>(undefined);
  const [errorsByApprovalId, setErrorsByApprovalId] = useState<Readonly<Record<string, string>>>({});

  const respondingApprovalIdsRef = useRef<Set<string>>(new Set());

  const setApprovalError = useCallback((approvalId: string, message: string): void => {
    setErrorsByApprovalId((prev) => ({ ...prev, [approvalId]: message }));
  }, []);

  const clearApprovalError = useCallback((approvalId: string): void => {
    setErrorsByApprovalId((prev) => {
      const next = { ...prev };
      delete next[approvalId];
      return next;
    });
  }, []);

  const addApproval = useCallback((request: ApprovalRequest): void => {
    setApprovals((prev) => [...prev.filter((item) => item.id !== request.id), request]);
  }, []);

  const mergeApprovals = useCallback((incoming: ApprovalRequest[]): void => {
    const incomingIds = new Set(incoming.map((a) => a.id));
    setApprovals((current) => incoming.reduce(
      (merged, approval) => [
        ...merged.filter((item) => item.id !== approval.id),
        approval,
      ],
      current,
    ));

    // Cleanup stale responding/error/dialog state for approvals no longer pending
    setRespondingApprovalIds((prev) => {
      const next = new Set([...prev].filter((id) => incomingIds.has(id)));
      respondingApprovalIdsRef.current = next;
      return next;
    });
    setRespondingKindByApprovalId((prev) => {
      const next: Record<string, "approve" | "reject"> = {};
      for (const [id, kind] of Object.entries(prev)) {
        if (incomingIds.has(id)) next[id] = kind;
      }
      return next;
    });
    setErrorsByApprovalId((prev) => {
      const next: Record<string, string> = {};
      for (const [id, err] of Object.entries(prev)) {
        if (incomingIds.has(id)) next[id] = err;
      }
      return next;
    });
    setFeedbackDialogApproval((prev) => {
      if (prev && !incomingIds.has(prev.id)) return null;
      return prev;
    });
  }, []);

  const clearApprovalsForRun = useCallback((runId: string): void => {
    setApprovals((prev) => {
      const removedIds = new Set(prev.filter((a) => a.runId === runId).map((a) => a.id));
      if (removedIds.size > 0) {
        setRespondingApprovalIds((curr) => {
          const next = new Set([...curr].filter((id) => !removedIds.has(id)));
          respondingApprovalIdsRef.current = next;
          return next;
        });
        setRespondingKindByApprovalId((curr) => {
          const next: Record<string, "approve" | "reject"> = {};
          for (const [id, kind] of Object.entries(curr)) {
            if (!removedIds.has(id)) next[id] = kind;
          }
          return next;
        });
        setErrorsByApprovalId((curr) => {
          const next: Record<string, string> = {};
          for (const [id, err] of Object.entries(curr)) {
            if (!removedIds.has(id)) next[id] = err;
          }
          return next;
        });
        setFeedbackDialogApproval((dialogApp) => (dialogApp && removedIds.has(dialogApp.id) ? null : dialogApp));
      }
      return prev.filter((a) => a.runId !== runId);
    });
  }, []);

  const approve = useCallback(async (request: ApprovalRequest): Promise<void> => {
    if (respondingApprovalIdsRef.current.has(request.id)) return;
    respondingApprovalIdsRef.current.add(request.id);
    setRespondingApprovalIds(new Set(respondingApprovalIdsRef.current));
    setRespondingKindByApprovalId((prev) => ({ ...prev, [request.id]: "approve" }));
    clearApprovalError(request.id);

    try {
      // Approve MUST NOT carry feedback
      const accepted = await window.eidosRuntime.respondApproval(request.id, "approve");
      if (!accepted) {
        setApprovalError(request.id, "This approval has expired or was already resolved.");
        return;
      }
      setApprovals((prev) => prev.filter((a) => a.id !== request.id));
      clearApprovalError(request.id);
    } catch (cause) {
      setApprovalError(request.id, userFacingError(cause) || "Approval failed. Try again.");
    } finally {
      respondingApprovalIdsRef.current.delete(request.id);
      setRespondingApprovalIds(new Set(respondingApprovalIdsRef.current));
      setRespondingKindByApprovalId((prev) => {
        const next = { ...prev };
        delete next[request.id];
        return next;
      });
    }
  }, [clearApprovalError, setApprovalError]);

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
    if (respondingApprovalIdsRef.current.has(request.id)) return;

    const trimmedFeedback = feedback.trim();
    const utf8Length = new TextEncoder().encode(trimmedFeedback).byteLength;
    if (utf8Length > MAX_APPROVAL_FEEDBACK_BYTES) {
      setFeedbackDialogError(`反馈长度超过限制 (${MAX_APPROVAL_FEEDBACK_BYTES} 字节)`);
      return;
    }

    respondingApprovalIdsRef.current.add(request.id);
    setRespondingApprovalIds(new Set(respondingApprovalIdsRef.current));
    setRespondingKindByApprovalId((prev) => ({ ...prev, [request.id]: "reject" }));
    setFeedbackDialogError(undefined);

    try {
      const accepted = await window.eidosRuntime.respondApproval(
        request.id,
        "reject",
        trimmedFeedback.length > 0 ? trimmedFeedback : undefined,
      );
      if (!accepted) {
        setFeedbackDialogError("This approval has expired or was already resolved.");
        return;
      }
      setApprovals((prev) => prev.filter((a) => a.id !== request.id));
      clearApprovalError(request.id);
      setFeedbackDialogApproval(null);
    } catch (cause) {
      setFeedbackDialogError(userFacingError(cause));
    } finally {
      respondingApprovalIdsRef.current.delete(request.id);
      setRespondingApprovalIds(new Set(respondingApprovalIdsRef.current));
      setRespondingKindByApprovalId((prev) => {
        const next = { ...prev };
        delete next[request.id];
        return next;
      });
    }
  }, [clearApprovalError]);

  const state: ApprovalControllerState = {
    approvals,
    respondingApprovalIds,
    respondingKindByApprovalId,
    feedbackDialogApproval,
    feedbackDialogError,
    errorsByApprovalId,
  };

  const actions: ApprovalControllerActions = {
    addApproval,
    mergeApprovals,
    clearApprovalsForRun,
    approve,
    openRejectDialog,
    closeFeedbackDialog,
    submitReject,
    setApprovalError,
    clearApprovalError,
  };

  return [state, actions];
}
