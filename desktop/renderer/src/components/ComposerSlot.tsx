import type { ReactNode } from "react";
import type { UserInputRequest } from "../../../shared/planning.generated.js";
import { ApprovalComposer, type ApprovalComposerProps } from "./ApprovalComposer.js";
import { ClarificationComposer } from "./ClarificationComposer.js";

export interface ComposerSlotProps extends Omit<ApprovalComposerProps, "run" | "approval"> {
  run: ApprovalComposerProps["run"] | null | undefined;
  approval: ApprovalComposerProps["approval"] | undefined;
  pendingUserInput?: UserInputRequest | undefined;
  /** A team proposal waiting for the user's roster decision. */
  pendingRoster?: ReactNode;
  userInputReady?: boolean;
  onAnswerUserInput?: () => void;
  children: ReactNode;
}

export function ComposerSlot({
  run,
  approval,
  pendingUserInput,
  pendingRoster,
  userInputReady = true,
  onAnswerUserInput,
  children,
  ...props
}: ComposerSlotProps) {
  // 1. Only replace the Session composer when human approval is loaded and genuinely required.
  //    Normal auto_review and approval syncing keep their live status inside the feed.
  const needsHumanApproval = run?.status === "waiting_approval"
    && run.approvalMode !== "full_access"
    && Boolean(approval)
    && (
      run.approvalMode !== "auto_review"
      || Boolean(approval?.reviewFallback)
      || Boolean(approval?.requiresHumanConfirmation)
    );
  if (needsHumanApproval && run && approval) {
    return <ApprovalComposer {...props} run={run} approval={approval} autoFocus />;
  }

  // 2. Pending user input replaces the ordinary composer.
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

  if (pendingRoster) return pendingRoster;

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
