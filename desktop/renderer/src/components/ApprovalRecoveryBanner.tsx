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
      <span className="approval-recovery-message">{error}</span>
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
