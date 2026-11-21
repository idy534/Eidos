import type { TeamMemberView, TeamTaskView } from "../../../../shared/teams.js";

export const MEMBER_ROLE_LABELS: Readonly<Record<string, string>> = {
  default: "通用",
  explorer: "调查",
  worker: "工作",
  verifier: "核查",
};

export const TASK_STATUS_LABELS: Record<TeamTaskView["status"], string> = {
  open: "待领取", in_progress: "进行中", in_review: "待审查", done: "已完成", canceled: "已取消",
};

export const TEAM_STATUS_LABELS = { active: "运行中", paused: "已暂停", dissolving: "正在解散", dissolved: "已解散" } as const;

export const ACTIVE_RUN = new Set(["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing"]);

export function formatCompactTokens(total: number, incomplete: boolean): string | undefined {
  if (total === 0 && !incomplete) return undefined;
  const value = total < 1000
    ? String(total)
    : total < 1_000_000
      ? `${(total / 1000).toFixed(total < 10_000 ? 1 : 0)}k`
      : `${(total / 1_000_000).toFixed(1)}M`;
  return `${incomplete ? "≥" : ""}${value}`;
}

export function formatTokens(total: number, incomplete: boolean): string | undefined {
  const compact = formatCompactTokens(total, incomplete);
  return compact ? `${compact} tokens` : undefined;
}

export function memberRolePillClass(role: string): string {
  if (role === "worker") return "agent-role-pill agent-role-pill--worker";
  if (role === "explorer") return "agent-role-pill agent-role-pill--explorer";
  if (role === "verifier") return "agent-role-pill team-role-pill--verifier";
  return "agent-role-pill agent-role-pill--default";
}

export function memberStatusDotClass(member: TeamMemberView): string {
  if (member.status === "removed") return "agent-status-dot agent-status-dot--stopped";
  if (member.runStatus && ACTIVE_RUN.has(member.runStatus)) return `agent-status-dot agent-status-dot--${member.runStatus}`;
  return "agent-status-dot team-dot--idle";
}
