import type { ReactNode } from "react";
import { ApprovalComposer, type ApprovalComposerProps } from "./ApprovalComposer.js";

export function ComposerSlot({ run, approval, children, ...props }:
  Omit<ApprovalComposerProps, "run" | "approval"> & {
    run: ApprovalComposerProps["run"] | null | undefined;
    approval: ApprovalComposerProps["approval"] | undefined;
    children: ReactNode;
  }) {
  if (run?.status !== "waiting_approval") return children;
  if (run.approvalMode === "auto_review" || run.approvalMode === "full_access") {
    return <><div role="status" className="approval-card approval-composer">
      {run.approvalMode === "auto_review" ? "模型正在审查操作…" : "正在处理完全访问授权…"}
    </div>{children}</>;
  }
  return approval
    ? <ApprovalComposer {...props} run={run} approval={approval} />
    : <div className="approval-card approval-composer" role="status">正在恢复待批准请求…</div>;
}
