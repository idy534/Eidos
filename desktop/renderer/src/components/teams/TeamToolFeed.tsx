import type { ReactNode, SyntheticEvent } from "react";
import type { Item, ToolCall } from "../../contracts.js";
import { RUNTIME_ERROR_MESSAGES } from "../../session-state.js";
import { TeamIcon } from "../InputPicker.js";
import { GitBranchIcon, HistoryIcon } from "../MenuIcons.js";
import { toolFilePaths } from "../ResultFiles.js";
import { MEMBER_ROLE_LABELS, TASK_STATUS_LABELS, memberRolePillClass } from "./team-labels.js";

const TEAM_COORDINATION_TOOLS = new Set([
  "team_propose",
  "team_add_member",
  "team_remove_member",
  "team_task",
  "team_post",
  "team_read",
  "team_wait",
]);

const TEAM_TOOL_NAMES = new Set([...TEAM_COORDINATION_TOOLS, "team_integrate"]);

export function isTeamTool(name: string): boolean {
  return TEAM_TOOL_NAMES.has(name);
}

export function isTeamCoordinationTool(name: string): boolean {
  return TEAM_COORDINATION_TOOLS.has(name);
}

// Local JSON parsing helpers --------------------------------------------------

function parseObj(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function objField(source: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = source[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function strField(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === "string" ? value : "";
}

function arrField(source: Record<string, unknown>, key: string): unknown[] {
  const value = source[key];
  return Array.isArray(value) ? value : [];
}

function compactLine(text: string, max = 36): string {
  const single = text.replace(/\s+/g, " ").trim();
  if (!single) return "";
  return single.length > max ? `${single.slice(0, max - 1)}…` : single;
}

function isTeamReconciliationGate(result: Record<string, unknown>): boolean {
  const code = strField(result, "code").toUpperCase();
  return code === "RECONCILIATION_REQUIRED" || code === "TOOL_RECONCILIATION_REQUIRED"
    || code.endsWith("_RECONCILIATION_REQUIRED");
}

function isTeamToolError(item: Item, toolCall: ToolCall, result: Record<string, unknown>): boolean {
  const nonSuccess = typeof result.outcome === "string" && result.outcome !== "success";
  return Boolean(
    nonSuccess
    || item.status === "failed"
    || item.status === "declined"
    || item.status === "canceled"
    || toolCall.status === "failed"
    || toolCall.status === "canceled"
    || isTeamReconciliationGate(result),
  );
}

const TEAM_ERROR_LABELS: Readonly<Record<string, string | undefined>> = {
  team_not_active: RUNTIME_ERROR_MESSAGES.TEAM_NOT_ACTIVE,
  team_member_has_changes: RUNTIME_ERROR_MESSAGES.TEAM_MEMBER_HAS_CHANGES,
  team_member_running: RUNTIME_ERROR_MESSAGES.TEAM_MEMBER_RUNNING,
  team_proposal_inactive: RUNTIME_ERROR_MESSAGES.TEAM_PROPOSAL_INACTIVE,
  team_task_not_found: "未找到对应任务",
  team_task_forbidden: "当前角色无权执行该任务操作",
  team_task_blocked: "任务仍被前置依赖阻塞",
  team_task_state_conflict: "任务状态已变化，无法执行该操作",
  team_task_version_conflict: "任务已被其他人修改，需要重新读取后再更新",
  team_task_key_exists: "任务标识已存在",
  team_task_limit: "团队任务数量已达上限",
  team_evidence_required: "提交验收需要附带工具执行证据",
  team_task_reason_required: "退回或重开任务需要填写原因",
  team_gate_milestone_required: "关口任务必须归属某个里程碑",
  team_member_not_found: "未找到对应成员",
  team_recipient_not_found: "消息收件人无效",
  team_direct_single_recipient: "私聊仅支持单名收件人",
  team_message_empty_or_too_large: "消息为空或超出长度限制",
  team_message_not_found: "未找到对应消息",
  team_member_limit: "团队成员数量已达上限",
  team_workspace_unavailable: RUNTIME_ERROR_MESSAGES.TEAM_WORKSPACE_UNAVAILABLE,
  team_integrate_target_invalid: "整合目标无效",
  delivery_failed: "未找到可整合的成员工作区变更",
};

function formatTeamError(result: Record<string, unknown>, status: Item["status"]): string {
  if (isTeamReconciliationGate(result)) return "未执行：与尚未确认的操作冲突";
  const code = strField(result, "code");
  const rawSummary = strField(result, "summary");
  if (code === "team_task_hook_blocked" && rawSummary) {
    return rawSummary.replace(/^A TeamTaskCompleted Hook kept the task open:\s*/i, "完成前校验未通过：");
  }
  const mapped = code ? TEAM_ERROR_LABELS[code] : undefined;
  if (mapped) return mapped;
  if (rawSummary && !rawSummary.startsWith("The request was not applied.")) return rawSummary;
  if (status === "declined") return "操作已拒绝";
  if (status === "canceled") return "操作已取消";
  return code || "执行失败";
}

function formatRecipient(token: string): string {
  if (token === "all") return "全体";
  if (token === "user") return "你";
  if (token === "lead") return "lead";
  return `@${token}`;
}

function formatRecipients(tokens: readonly string[], toAll: boolean): string {
  if (toAll || tokens.includes("all")) return "全体";
  const formatted = tokens.filter(Boolean).map(formatRecipient);
  if (formatted.length === 0) return "团队";
  if (formatted.length <= 2) return formatted.join("、");
  return `${formatted.slice(0, 2).join("、")} 等 ${formatted.length} 人`;
}

// Semantic Icons (14x14, 1.3px stroke matching ExecutionFeed) -----------------

function TeamTaskToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2.5" y="2.5" width="11" height="11" rx="2.5" />
      <path d="M5.5 8.1l1.7 1.7 3.3-3.5" />
    </svg>
  );
}

function TeamMessageToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4.2A1.7 1.7 0 0 1 4.7 2.5h6.6A1.7 1.7 0 0 1 13 4.2v4.9a1.7 1.7 0 0 1-1.7 1.7H7.2L4.2 13.3V10.8H4.7A1.7 1.7 0 0 1 3 9.1V4.2z" />
    </svg>
  );
}

function TeamDirectToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3.5" y="7" width="9" height="6.5" rx="1.6" />
      <path d="M5.6 7V5.2a2.4 2.4 0 0 1 4.8 0V7" />
    </svg>
  );
}

function TeamReadToolIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 2h5.5L13 5.5V14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
      <path d="M9.5 2v3.5H13" />
      <path d="M6 8.5h4" />
      <path d="M6 11.5h3" />
    </svg>
  );
}

function TeamWaitingDots() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true">
      <circle cx="5" cy="5.5" r="1.3" />
      <circle cx="5" cy="10.5" r="1.3" />
      <circle cx="11" cy="5.5" r="1.3" />
      <circle cx="11" cy="10.5" r="1.3" />
    </svg>
  );
}

export function teamToolIcon(toolName: string, argumentsJson?: string, resultJson?: string): ReactNode {
  if (toolName === "team_propose" || toolName === "team_add_member" || toolName === "team_remove_member") {
    return <TeamIcon />;
  }
  if (toolName === "team_task") return <TeamTaskToolIcon />;
  if (toolName === "team_post") {
    const args = parseObj(argumentsJson);
    const data = objField(parseObj(resultJson), "data");
    const message = objField(data, "message");
    const channel = strField(message, "channel") || strField(args, "channel");
    return channel === "direct" ? <TeamDirectToolIcon /> : <TeamMessageToolIcon />;
  }
  if (toolName === "team_read") return <TeamReadToolIcon />;
  if (toolName === "team_wait") return <HistoryIcon />;
  if (toolName === "team_integrate") return <GitBranchIcon />;
  return <TeamIcon />;
}

// Human-readable single-line summaries ---------------------------------------

const TASK_OP_VERBS: Readonly<Record<string, { running: string; done: string; base: string }>> = {
  create: { running: "正在新建任务", done: "已新建任务", base: "新建任务" },
  update: { running: "正在更新任务", done: "已更新任务", base: "更新任务" },
  assign: { running: "正在指派任务", done: "已指派任务", base: "指派任务" },
  claim: { running: "正在领取任务", done: "已领取任务", base: "领取任务" },
  submit: { running: "正在提交任务", done: "已提交验收", base: "提交任务" },
  accept: { running: "正在通过验收", done: "已通过验收", base: "通过验收" },
  reject: { running: "正在退回任务", done: "已退回任务", base: "退回任务" },
  release: { running: "正在释放任务", done: "已释放任务", base: "释放任务" },
  reopen: { running: "正在重新打开任务", done: "已重新打开任务", base: "重新打开任务" },
  cancel: { running: "正在取消任务", done: "已取消任务", base: "取消任务" },
};

function nonTerminalPrefix(status: Item["status"]): string {
  return ({ failed: "失败", declined: "已拒绝", canceled: "已取消", in_progress: "运行中", completed: "成功" } as const)[status];
}

export function teamToolSummary(toolCall: ToolCall, status: Item["status"]): string {
  const args = parseObj(toolCall.argumentsJson);
  const result = parseObj(toolCall.resultJson);
  const data = objField(result, "data");
  const running = status === "in_progress";
  const completed = status === "completed" && result.outcome !== "error";

  if (isTeamReconciliationGate(result)) {
    return `未执行：与尚未确认的操作冲突 ${teamBaseName(toolCall.toolName)}`;
  }

  switch (toolCall.toolName) {
    case "team_propose": {
      const team = objField(data, "team");
      const teamMembers = arrField(team, "members");
      const proposedMembers = arrField(args, "members");
      const count = teamMembers.length || proposedMembers.length;
      if (running) return count > 0 ? `正在提议团队编制 · ${count} 名成员` : "正在提议团队编制";
      if (!completed) return `${nonTerminalPrefix(status)} 组建团队`;
      if (strField(result, "code") === "team_proposal_declined") return "已取消组建团队";
      return count > 0 ? `已组建团队 · ${count} 名成员` : "已组建团队";
    }

    case "team_task": {
      const op = strField(args, "op");
      const task = objField(data, "task");
      const title = compactLine(strField(task, "title") || strField(args, "title") || strField(args, "key"), 44);
      const owner = strField(args, "owner") || strField(task, "owner");
      const verbEntry = TASK_OP_VERBS[op] ?? { running: "正在更新任务", done: "已更新任务", base: "更新任务" };
      let verb = running ? verbEntry.running : verbEntry.done;
      if (!running && completed && op === "submit" && strField(task, "status") === "done") {
        verb = "已完成任务";
      }
      if (!running && !completed) {
        return title ? `${nonTerminalPrefix(status)} ${verbEntry.base} · ${title}` : `${nonTerminalPrefix(status)} ${verbEntry.base}`;
      }
      const assignSuffix = op === "assign" && owner ? ` → @${owner}` : "";
      return title ? `${verb} · ${title}${assignSuffix}` : `${verb}${assignSuffix}`;
    }

    case "team_post": {
      const message = objField(data, "message");
      const channel = strField(message, "channel") || strField(args, "channel") || "board";
      const toAll = message.toAll === true;
      const rawRecipients = arrField(message, "recipients").length > 0
        ? arrField(message, "recipients")
        : arrField(args, "to");
      const recipients = rawRecipients.filter((entry): entry is string => typeof entry === "string");
      const target = formatRecipients(recipients, toAll);
      const preview = compactLine(strField(message, "content") || strField(args, "text"), 34);
      const isDirect = channel === "direct";
      if (!running && !completed) {
        return `${nonTerminalPrefix(status)} ${isDirect ? `私聊 ${target}` : `发送看板消息 · ${target}`}`;
      }
      const head = isDirect
        ? `${running ? "正在私聊" : "已私聊"} ${target}`
        : `${running ? "正在发送看板消息" : "已发送看板消息"} · ${target}`;
      return preview ? `${head} · ${preview}` : head;
    }

    case "team_read": {
      const kind = strField(args, "kind");
      const id = strField(args, "id");
      if (kind === "tasks") {
        const list = arrField(data, "tasks");
        if (running) return "正在查看任务板";
        if (!completed) return `${nonTerminalPrefix(status)} 查看任务板`;
        return list.length > 0 ? `已查看任务板（${list.length} 项任务）` : "已查看任务板";
      }
      if (kind === "task") {
        const task = objField(data, "task");
        const label = compactLine(strField(task, "title") || id, 40);
        if (running) return label ? `正在查看任务 · ${label}` : "正在查看任务";
        if (!completed) return `${nonTerminalPrefix(status)} 查看任务${label ? ` · ${label}` : ""}`;
        return label ? `已查看任务 · ${label}` : "已查看任务";
      }
      if (kind === "messages") {
        const page = objField(data, "messages");
        const list = arrField(page, "messages");
        if (running) return "正在查看团队消息";
        if (!completed) return `${nonTerminalPrefix(status)} 查看团队消息`;
        return `已查看团队消息（${list.length > 0 ? `${list.length} 条` : "无新消息"}）`;
      }
      if (kind === "message") {
        if (running) return "正在查看消息详情";
        if (!completed) return `${nonTerminalPrefix(status)} 查看消息详情`;
        return "已查看消息详情";
      }
      if (kind === "member") {
        const member = objField(data, "member");
        const name = strField(member, "name") || id;
        if (running) return name ? `正在查看成员状态 · @${name}` : "正在查看成员状态";
        if (!completed) return `${nonTerminalPrefix(status)} 查看成员状态${name ? ` · @${name}` : ""}`;
        return name ? `已查看成员状态 · @${name}` : "已查看成员状态";
      }
      return running ? "正在查看团队状态" : completed ? "已查看团队状态" : `${nonTerminalPrefix(status)} 查看团队状态`;
    }

    case "team_wait": {
      const memberList = arrField(args, "members").filter((entry): entry is string => typeof entry === "string");
      const taskList = arrField(args, "tasks").filter((entry): entry is string => typeof entry === "string");
      let targetDesc = "团队进展";
      if (memberList.length === 1) targetDesc = `@${memberList[0]}`;
      else if (memberList.length === 2) targetDesc = `@${memberList[0]}、@${memberList[1]}`;
      else if (memberList.length > 2) targetDesc = `@${memberList[0]} 等 ${memberList.length} 人`;
      else if (taskList.length === 1) targetDesc = `任务 ${taskList[0]}`;
      else if (taskList.length > 1) targetDesc = `${taskList.length} 项任务`;

      if (running) {
        return memberList.length > 0 || taskList.length > 0
          ? `正在等待 ${targetDesc} 完成`
          : "正在等待团队进展";
      }
      if (!completed) return `${nonTerminalPrefix(status)} 等待团队进展`;
      const reason = strField(data, "waitReason") || strField(data, "wait_reason");
      const reasonLabel = reason === "message"
        ? "收到新消息"
        : reason === "targets_ready"
          ? "已就绪"
          : reason === "user_input"
            ? "收到你的消息"
            : reason === "timeout"
              ? "已超时"
              : "";
      const prefix = targetDesc === "团队进展" ? "等待团队进展" : `等待 ${targetDesc}`;
      return reasonLabel ? `${prefix} · ${reasonLabel}` : `已${prefix}`;
    }

    case "team_add_member": {
      const member = objField(data, "member");
      const name = strField(member, "name") || strField(args, "name");
      const role = strField(member, "role") || strField(args, "role");
      const roleLabel = role ? (MEMBER_ROLE_LABELS[role] ?? role) : "";
      if (running) return name ? `正在添加成员 · @${name}` : "正在添加成员";
      if (!completed) return `${nonTerminalPrefix(status)} 添加成员${name ? ` · @${name}` : ""}`;
      return name ? `已添加成员 · @${name}${roleLabel ? `（${roleLabel}）` : ""}` : "已添加成员";
    }

    case "team_remove_member": {
      const name = strField(args, "name");
      const discard = args.discardChanges === true || args.discard_changes === true;
      if (running) return name ? `正在移除成员 · @${name}` : "正在移除成员";
      if (!completed) return `${nonTerminalPrefix(status)} 移除成员${name ? ` · @${name}` : ""}`;
      return name
        ? `已移除成员 · @${name}${discard ? "（放弃未整合变更）" : ""}`
        : "已移除成员";
    }

    case "team_integrate": {
      const memberName = strField(args, "member");
      const taskKey = strField(args, "task");
      const target = memberName ? `@${memberName}` : taskKey ? `任务 ${taskKey}` : "成员";
      const changedFiles = toolFilePaths(toolCall).length || arrField(data, "files").length || arrField(args, "paths").length;
      if (running) return `正在整合 ${target} 的变更`;
      if (!completed) return `${nonTerminalPrefix(status)} 整合 ${target} 的变更`;
      return changedFiles > 0 ? `已整合 ${target} 的变更（${changedFiles} 个文件）` : `已整合 ${target} 的变更`;
    }

    default:
      return running ? `正在运行 ${toolCall.toolName}` : `已运行 ${toolCall.toolName}`;
  }
}

function teamBaseName(toolName: string): string {
  const map: Record<string, string> = {
    team_propose: "组建团队",
    team_add_member: "添加成员",
    team_remove_member: "移除成员",
    team_task: "更新任务",
    team_post: "发送团队消息",
    team_read: "查看团队状态",
    team_wait: "等待团队进展",
    team_integrate: "整合变更",
  };
  return map[toolName] ?? toolName;
}

// Team Tool Item Renderer ----------------------------------------------------

export function TeamToolItem({
  item,
  toolCall,
  details,
}: {
  item: Item;
  toolCall: ToolCall;
  details: { open: boolean; mounted: boolean; onToggle: (event: SyntheticEvent<HTMLDetailsElement>) => void };
}) {
  const args = parseObj(toolCall.argumentsJson);
  const result = parseObj(toolCall.resultJson);
  const data = objField(result, "data");
  const isError = isTeamToolError(item, toolCall, result);
  const icon = teamToolIcon(toolCall.toolName, toolCall.argumentsJson, toolCall.resultJson);
  const summaryText = teamToolSummary(toolCall, item.status);

  // Special waiting card for team_propose while awaiting user roster confirmation
  if (toolCall.toolName === "team_propose" && item.status === "in_progress") {
    return (
      <div className="tool-item tool-item--user-input tool-item--user-input-waiting">
        <div className="tool-user-input-header">
          <span className="tool-icon tool-icon--team-accent" aria-hidden="true">{icon}</span>
          <span className="tool-user-input-title">{summaryText}</span>
        </div>
        <div className="tool-user-input-waiting-row">
          <span className="tool-waiting-dots" aria-hidden="true"><TeamWaitingDots /></span>
          <span className="tool-waiting-text">正在等待你确认团队编制</span>
        </div>
      </div>
    );
  }

  const body = isError
    ? <p className="tool-summary--error">{formatTeamError(result, item.status)}</p>
    : renderTeamToolBody(toolCall.toolName, args, result, data);

  // Static single-line rendering when there is no meaningful secondary content
  if (!body) {
    return (
      <div className="tool-item tool-item--read-done">
        <span className="tool-icon" aria-hidden="true">{icon}</span>
        <span>{summaryText}</span>
      </div>
    );
  }

  return (
    <details className="tool-item tool-item--team" open={details.open} onToggle={details.onToggle}>
      <summary>
        <span className="tool-icon" aria-hidden="true">{icon}</span>
        <span>{summaryText}</span>
      </summary>
      {details.mounted && <div className="tool-body tool-team-body">{body}</div>}
    </details>
  );
}

function renderTeamToolBody(
  toolName: string,
  args: Record<string, unknown>,
  result: Record<string, unknown>,
  data: Record<string, unknown>,
): ReactNode {
  if (toolName === "team_read" || toolName === "team_wait") {
    return null;
  }

  if (toolName === "team_propose") {
    if (strField(result, "code") === "team_proposal_declined") return null;
    const team = objField(data, "team");
    const objective = strField(team, "objective") || strField(args, "objective");
    const rawMembers = arrField(team, "members").length > 0 ? arrField(team, "members") : arrField(args, "members");
    const memberEntries = rawMembers
      .filter((entry): entry is Record<string, unknown> => Boolean(entry && typeof entry === "object"))
      .map((entry) => ({
        name: strField(entry, "name"),
        role: strField(entry, "role") || "worker",
        worktree: strField(entry, "workspaceMode") === "worktree" || strField(entry, "workspace") === "worktree",
      }))
      .filter((entry) => Boolean(entry.name));
    const taskCount = arrField(team, "tasks").length || arrField(args, "tasks").length;
    if (!objective && memberEntries.length === 0) return null;

    return (
      <>
        {objective && <p className="tool-team-objective">{objective}</p>}
        {(memberEntries.length > 0 || taskCount > 0) && (
          <div className="tool-team-chips">
            {memberEntries.map((entry) => (
              <span key={entry.name} className={memberRolePillClass(entry.role)}>
                <span>{entry.name}</span>
                <span>· {MEMBER_ROLE_LABELS[entry.role] ?? entry.role}</span>
                {entry.worktree && <GitBranchIcon width={10} height={10} />}
              </span>
            ))}
            {taskCount > 0 && <span className="tool-team-chip">{taskCount} 项初始任务</span>}
          </div>
        )}
      </>
    );
  }

  if (toolName === "team_task") {
    const task = objField(data, "task");
    const key = strField(task, "key") || strField(args, "key");
    const status = strField(task, "status") as keyof typeof TASK_STATUS_LABELS | "";
    const statusText = status && TASK_STATUS_LABELS[status] ? TASK_STATUS_LABELS[status] : "";
    const owner = strField(task, "owner") || strField(args, "owner");
    const reviewer = strField(task, "reviewer") || strField(args, "reviewer");
    const milestone = strField(task, "milestone") || strField(args, "milestone");
    const evidenceCount = arrField(task, "evidence").length || arrField(args, "evidence").length;
    const note = strField(args, "note") || strField(task, "note") || strField(args, "description");
    const op = strField(args, "op");
    const hasRichDetails = Boolean(note || evidenceCount > 0 || milestone || (op === "create" && (owner || reviewer)));
    if (!hasRichDetails) return null;

    return (
      <>
        <div className="tool-team-meta">
          {key && <code className="tool-team-code">#{key}</code>}
          {statusText && <span>{statusText}</span>}
          {owner && <span>· 负责人 @{owner}</span>}
          {reviewer && <span>· 审查者 @{reviewer}</span>}
          {milestone && <span>· {milestone}</span>}
          {evidenceCount > 0 && <span>· {evidenceCount} 项证据</span>}
        </div>
        {note && <p className="tool-team-text">{note}</p>}
      </>
    );
  }

  if (toolName === "team_post") {
    const message = objField(data, "message");
    const content = strField(message, "content") || strField(args, "text");
    if (!content) return null;
    const channel = strField(message, "channel") || strField(args, "channel") || "board";
    const toAll = message.toAll === true;
    const rawRecipients = arrField(message, "recipients").length > 0
      ? arrField(message, "recipients")
      : arrField(args, "to");
    const recipients = rawRecipients.filter((entry): entry is string => typeof entry === "string");
    const target = formatRecipients(recipients, toAll);
    const taskKey = strField(message, "task") || strField(args, "task");

    return (
      <>
        <div className="tool-team-meta">
          <span className="tool-team-chip">{channel === "direct" ? "私聊" : "公开看板"}</span>
          <span>发给 {target}</span>
          {taskKey && <code className="tool-team-code">#{taskKey}</code>}
        </div>
        <p className="tool-team-text">{content}</p>
      </>
    );
  }

  if (toolName === "team_add_member") {
    const member = objField(data, "member");
    const brief = strField(member, "brief") || strField(args, "brief");
    const role = strField(member, "role") || strField(args, "role") || "worker";
    const worktree = strField(member, "workspaceMode") === "worktree" || strField(args, "workspace") === "worktree";
    if (!brief) return null;

    return (
      <>
        <div className="tool-team-meta">
          <span className={memberRolePillClass(role)}>{MEMBER_ROLE_LABELS[role] ?? role}</span>
          <span>{worktree ? "独立 Worktree" : "共享工作区"}</span>
        </div>
        <p className="tool-team-text">{brief}</p>
      </>
    );
  }

  if (toolName === "team_remove_member") {
    const reason = strField(args, "reason");
    if (!reason) return null;
    return <p className="tool-team-text">{reason}</p>;
  }

  return null;
}
