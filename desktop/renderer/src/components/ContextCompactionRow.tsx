import type { ContextCompactionActivity } from "../../../shared/context.generated.js";
import type { Run } from "../contracts.js";

export function CompactionIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M8 1.5v3.5M5.75 3.5L8 5.25l2.25-1.75" />
      <path d="M2.5 8h2M6.25 8h3.5M11.5 8h2" />
      <path d="M8 14.5v-3.5m-2.25 1.75L8 10.75l2.25 1.75" />
    </svg>
  );
}

export function ContextCompactionRow({ activity, run }: { activity: ContextCompactionActivity; run: Run }) {
  const active = ["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing"].includes(run.status);
  const running = activity.status === "running" && active;
  const label = running ? "上下文正在压缩"
    : activity.status === "succeeded" ? (activity.trigger && activity.trigger !== "manual" ? "上下文已自动压缩" : "上下文已压缩")
    : activity.status === "canceled" || (activity.status === "running" && run.status === "canceled") ? "上下文压缩已取消"
    : activity.reason === "no_compactable_history" ? "上下文压缩已跳过"
    : activity.status === "running" ? "上下文压缩已中断" : "上下文压缩未完成";
  return <div className="tool-item tool-item--read-done context-compaction" data-compaction-id={activity.requestId}
    role={running ? "status" : undefined}>
    <span className="tool-icon" aria-hidden="true">
      <CompactionIcon />
    </span>
    <span>{label}</span>
  </div>;
}
