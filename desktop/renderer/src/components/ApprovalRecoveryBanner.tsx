import { Button } from "./Button.js";

interface ApprovalRecoveryBannerProps {
  error: string;
  loading?: boolean | undefined;
  onRetry: () => void;
}

export function ApprovalRecoveryBanner({
  error,
  loading = false,
  onRetry,
}: ApprovalRecoveryBannerProps) {
  return (
    <div className="approval-recovery-banner" role="alert">
      <div className="approval-recovery-banner__main">
        <svg
          className="approval-recovery-banner__icon"
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        <span className="approval-recovery-message">{error}</span>
      </div>
      <Button
        variant="secondary"
        size="small"
        disabled={loading}
        loading={loading}
        onClick={onRetry}
        aria-label="重试加载审批"
      >
        {loading ? "加载中…" : "重试加载审批"}
      </Button>
    </div>
  );
}
