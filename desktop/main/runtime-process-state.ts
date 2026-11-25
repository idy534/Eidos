import type { ApprovalDecision, ApprovalRequest } from "../shared/index.js";

export interface RuntimeActiveRunEntry {
  sessionId: string;
  status: string;
  title?: string;
}

export interface PendingApprovalEntry {
  request: ApprovalRequest;
  resolve: (decision: ApprovalDecision) => void;
}

export function clearRuntimeState(
  activeRuns: Map<string, RuntimeActiveRunEntry>,
  pendingApprovals: Map<string, PendingApprovalEntry>,
): void {
  activeRuns.clear();
  for (const [id, pending] of pendingApprovals) {
    pendingApprovals.delete(id);
    try {
      pending.resolve({ decision: "reject" });
    } catch {
      // Best-effort cleanup during runtime teardown.
    }
  }
}
