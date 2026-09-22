import type { ReactNode } from "react";
import type { UserInputRequest } from "../../../shared/planning.generated.js";
import { ApprovalComposer, type ApprovalComposerProps } from "./ApprovalComposer.js";
import { ClarificationComposer } from "./ClarificationComposer.js";

export interface ComposerSlotProps extends Omit<ApprovalComposerProps, "run" | "approval"> {
  run: ApprovalComposerProps["run"] | null | undefined;
  approval: ApprovalComposerProps["approval"] | undefined;
  pendingUserInput?: UserInputRequest | undefined;
  userInputReady?: boolean;
  onAnswerUserInput?: () => void;
  children: ReactNode;
}

export function ComposerSlot({
  run,
  approval,
  pendingUserInput,
  userInputReady = true,
  onAnswerUserInput,
  children,
  ...props
}: ComposerSlotProps) {
  // 1. Waiting Approval has priority
  if (run?.status === "waiting_approval") {
    if (run.approvalMode === "auto_review" || run.approvalMode === "full_access") {
      return (
        <>
          <ApprovalStatusBanner
            mode={run.approvalMode}
            title={run.approvalMode === "auto_review" ? "模型正在审查操作…" : "正在处理完全访问授权…"}
            hint={run.approvalMode === "auto_review" ? "正在评估工具调用的安全性与潜在风险" : "准备以系统直接权限执行当前操作"}
          />
          {children}
        </>
      );
    }
    return approval
      ? <ApprovalComposer {...props} run={run} approval={approval} />
      : (
        <ApprovalStatusBanner
          mode="manual"
          title="正在恢复待批准请求…"
          hint="正在同步审批详情与变更内容"
        />
      );
  }

  // 2. Pending User Input Clarification replaces the input box
  if (pendingUserInput) {
    return (
      <ClarificationComposer
        key={pendingUserInput.id}
        request={pendingUserInput}
        ready={userInputReady}
        onSaved={onAnswerUserInput ?? (() => {})}
      />
    );
  }

  // 3. Waiting input but question details not yet loaded
  if (run?.status === "waiting_input") {
    return (
      <ClarificationStatusBanner
        title="正在准备澄清问题…"
        hint="正在同步需要补充的信息"
      />
    );
  }

  // 4. Default normal Composer
  return children;
}

function ApprovalStatusBanner({
  mode,
  title,
  hint,
}: {
  mode: "auto_review" | "full_access" | "manual";
  title: string;
  hint: string;
}) {
  const isDanger = mode === "full_access";
  return (
    <div
      role="status"
      className={`approval-status-banner${isDanger ? " approval-status-banner--danger" : ""}`}
    >
      <div className="approval-status-banner__main">
        <div className="approval-status-banner__icon-wrap">
          <svg
            className="approval-status-banner__spinner"
            viewBox="0 0 16 16"
            width="14"
            height="14"
            fill="none"
            aria-hidden="true"
          >
            <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.8" />
            <path
              d="M8 2.5C11.0376 2.5 13.5 4.96243 13.5 8"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </div>
        <div className="approval-status-banner__content">
          <span className="approval-status-banner__title">{title}</span>
          <span className="approval-status-banner__hint">{hint}</span>
        </div>
      </div>
      <div className="approval-status-banner__badge">
        {mode === "auto_review" && <span>替我审批</span>}
        {mode === "full_access" && <span>完全访问</span>}
        {mode === "manual" && <span>请求审批</span>}
      </div>
    </div>
  );
}

function ClarificationStatusBanner({
  title,
  hint,
}: {
  title: string;
  hint: string;
}) {
  return (
    <div role="status" className="approval-status-banner">
      <div className="approval-status-banner__main">
        <div className="approval-status-banner__icon-wrap">
          <svg
            className="approval-status-banner__spinner"
            viewBox="0 0 16 16"
            width="14"
            height="14"
            fill="none"
            aria-hidden="true"
          >
            <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.8" />
            <path
              d="M8 2.5C11.0376 2.5 13.5 4.96243 13.5 8"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            />
          </svg>
        </div>
        <div className="approval-status-banner__content">
          <span className="approval-status-banner__title">{title}</span>
          <span className="approval-status-banner__hint">{hint}</span>
        </div>
      </div>
      <div className="approval-status-banner__badge">
        <span>补充信息</span>
      </div>
    </div>
  );
}
