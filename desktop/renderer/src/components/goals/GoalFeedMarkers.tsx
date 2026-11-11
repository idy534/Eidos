import type { ReactNode } from "react";
import type { GoalActivity, GoalRunOrigin } from "../../../../shared/goals.js";
import { GoalBlockedIcon, GoalCheckIcon, GoalClearIcon, GoalLoopIcon, GoalPausedIcon, GoalPlayIcon } from "./GoalIcons.js";

/** Quiet line before an automatic goal Run. The user's own Runs need no marker. */
export function GoalRunMarker({ origin }: { origin: GoalRunOrigin | undefined }) {
  if (!origin || origin.trigger === "user_input") return null;
  const text = origin.trigger === "goal_set" ? "目标执行" : "目标续跑";
  return <p className="goal-feed-marker" title={origin.trigger === "goal_set" ? "目标执行" : "目标自动续跑"}><GoalLoopIcon />{text} · 第 {origin.sequence} 轮</p>;
}

const statusMarkers: Record<string, { icon: ReactNode; text: string } | undefined> = {
  active: { icon: <GoalPlayIcon />, text: "目标已继续" },
  paused: { icon: <GoalPausedIcon />, text: "目标已暂停续跑" },
  blocked: { icon: <GoalBlockedIcon />, text: "目标受阻" },
  complete: { icon: <GoalCheckIcon />, text: "目标已完成" },
  cleared: { icon: <GoalClearIcon />, text: "目标已清除" },
};

/** Only state changes earn a Feed line; creation and edits are visible in the goal card. */
export function GoalActivityMarkers({ activities }: { activities: readonly GoalActivity[] }) {
  return <>{activities.filter((activity) => activity.kind === "status").map((activity) => {
    const marker = statusMarkers[activity.status];
    return marker ? <p key={`goal-${activity.eventId}`} className="goal-feed-marker" data-tone={activity.status}>{marker.icon}{marker.text}</p> : null;
  })}</>;
}
