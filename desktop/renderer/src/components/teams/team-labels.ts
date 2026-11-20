import type { TeamTaskView } from "../../../../shared/teams.js";
import { agentRoleLabels } from "../settings/AgentEditorDialog.js";

export const MEMBER_ROLE_LABELS: Readonly<Record<string, string>> = agentRoleLabels;

export const TASK_STATUS_LABELS: Record<TeamTaskView["status"], string> = {
  open: "待领取", in_progress: "进行中", in_review: "待审查", done: "已完成", canceled: "已取消",
};

export const TEAM_STATUS_LABELS = { active: "运行中", paused: "已暂停", dissolving: "正在解散", dissolved: "已解散" } as const;

export const ACTIVE_RUN = new Set(["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing"]);

export function formatTokens(total: number, incomplete: boolean): string | undefined {
  if (total === 0 && !incomplete) return undefined;
  const value = total >= 1000 ? `${(total / 1000).toFixed(total >= 100_000 ? 0 : 1)}k` : String(total);
  return `${incomplete ? "≥" : ""}${value} tokens`;
}
