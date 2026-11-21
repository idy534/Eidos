import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type {
  TeamMemberChanges, TeamMemberView, TeamMessageView, TeamTaskView, TeamUsage, TeamView,
} from "../../../../shared/teams.js";
import type { ApprovalRequest, ModelOption } from "../../contracts.js";
import { RUNTIME_ERROR_MESSAGES, userFacingError } from "../../session-state.js";
import { AgentTranscript, formatAgentModelDisplay } from "../AgentPanel.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { statusText, UpArrowIcon } from "../Composer.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { DropdownMenu, type DropdownMenuItem } from "../DropdownMenu.js";
import { EmptyState } from "../EmptyState.js";
import { TeamIcon } from "../InputPicker.js";
import {
  ChevronDownIcon, ChevronRightIcon, CloseIcon, FileDiffIcon, GitBranchIcon, HistoryIcon, MoreHorizontalIcon, TaskScopeIcon,
} from "../MenuIcons.js";
import { MarkdownContent } from "../MarkdownContent.js";
import { Select } from "../Select.js";
import { Tooltip } from "../Tooltip.js";
import { GoalCheckIcon, GoalPauseIcon, GoalPlayIcon } from "../goals/GoalIcons.js";
import { AgentRoleIcon } from "../settings/AgentEditorDialog.js";
import { RefreshIcon } from "../settings/ChannelIcons.js";
import { StatusBadge, type StatusTone } from "../settings/StatusBadge.js";
import {
  ACTIVE_RUN, MEMBER_ROLE_LABELS, TASK_STATUS_LABELS, TEAM_STATUS_LABELS,
  formatTokens, memberRolePillClass, memberStatusDotClass,
} from "./team-labels.js";
import type { useTeam } from "./useTeam.js";

type TeamActions = Pick<ReturnType<typeof useTeam>, "execute" | "refresh" | "revision" | "busy" | "error">;
type View = "board" | "members" | "tasks";
type TaskGroupBy = "milestone" | "status";

const WIDE_PANEL_MIN_PX = 760;
const TIME_DIVIDER_GAP_MS = 5 * 60 * 1000;
const MESSAGE_COLLAPSE_PX = 68;
const TASK_ORDER: TeamTaskView["status"][] = ["in_review", "in_progress", "open", "done", "canceled"];
const MESSAGE_PAGE = 100;
const MESSAGE_PAGES = 10;

function members(team: TeamView): TeamMemberView[] { return team.members ?? []; }
function tasks(team: TeamView): TeamTaskView[] { return team.tasks ?? []; }

function totalUsage(usage: readonly TeamUsage[]): { total: number; incomplete: boolean } {
  let total = 0;
  let incomplete = false;
  for (const value of usage) {
    total += (value.inputTokens ?? 0) + (value.outputTokens ?? 0);
    if (value.incomplete) incomplete = true;
  }
  return { total, incomplete };
}

type MemberStateTone = "running" | "waiting" | "idle" | "removed";

function memberState(member: TeamMemberView): string {
  if (member.status === "removed") return "已移除";
  return member.runStatus && ACTIVE_RUN.has(member.runStatus) ? statusText(member.runStatus) : "空闲";
}

function memberStateTone(member: TeamMemberView): MemberStateTone {
  if (member.status === "removed") return "removed";
  if (member.runStatus === "waiting_approval" || member.runStatus === "waiting_input") return "waiting";
  if (member.runStatus && ACTIVE_RUN.has(member.runStatus)) return "running";
  return "idle";
}

interface TooltipRow {
  label: string;
  value: string;
  tone?: "running" | "waiting" | "muted" | undefined;
}

function TeamTooltipCard({ title, badge, rows }: {
  title: string;
  badge?: string | undefined;
  rows: readonly TooltipRow[];
}) {
  return (
    <div className="team-tooltip-card">
      <div className="team-tooltip-card__head">
        <span className="team-tooltip-card__title">{title}</span>
        {badge && <span className="team-tooltip-card__badge">{badge}</span>}
      </div>
      {rows.length > 0 && (
        <dl className="team-tooltip-card__rows">
          {rows.map((row) => (
            <div key={row.label} className="team-tooltip-card__row">
              <dt>{row.label}</dt>
              <dd className={row.tone ? `team-tooltip-card__value--${row.tone}` : undefined}>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function resolveModelLabel(modelId: string | undefined, models?: readonly ModelOption[]): string | undefined {
  if (!modelId) return undefined;
  return formatAgentModelDisplay(undefined, modelId, models)?.fullDisplay ?? modelId;
}

function activeOwnedTask(allTasks: readonly TeamTaskView[], memberName: string): TeamTaskView | undefined {
  const owned = allTasks.filter((task) => task.owner === memberName);
  return owned.find((task) => task.status === "in_progress")
    ?? owned.find((task) => task.status === "in_review")
    ?? owned.find((task) => task.status === "open" && !task.blocked);
}

function deliveryLabel(status: string | null | undefined): string | undefined {
  if (status === "integrated") return "已整合";
  if (status === "partially_integrated") return "部分整合";
  if (status) return "待整合";
  return undefined;
}

function LockIcon({ size = 11 }: { size?: number }) {
  return (
    <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3.5" y="7" width="9" height="6.5" rx="1.5" />
      <path d="M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7" />
    </svg>
  );
}

function ChevronLeftIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 3.5 5.5 8l4.5 4.5" />
    </svg>
  );
}

function useWidePanel(ref: React.RefObject<HTMLElement | null>): boolean {
  const [isWide, setIsWide] = useState(false);

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    const update = (width: number) => {
      if (width > 0) setIsWide(width >= WIDE_PANEL_MIN_PX);
    };
    update(node.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) update(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);

  return isWide;
}

export interface TeamPanelProps {
  team: TeamView;
  usage: readonly TeamUsage[];
  actions: TeamActions;
  memberName?: string | undefined;
  onOpenMember: (name: string) => void;
  onBack: () => void;
  onCancelRun: (runId: string, sessionId: string) => void;
  approvals: ApprovalRequest[];
  respondingApprovalIds?: ReadonlySet<string>;
  respondingKindByApprovalId?: Readonly<Record<string, "approve" | "reject">>;
  expiredApprovalIds?: ReadonlySet<string>;
  errorsByApprovalId?: Readonly<Record<string, string>>;
  onApprove: (request: ApprovalRequest) => void;
  onReject: (request: ApprovalRequest) => void;
  onOpenFile?: ((path: string) => void) | undefined;
  workspaceRoot?: string | undefined;
  models?: readonly ModelOption[] | undefined;
}

/**
 * The lead Session's team panel:
 * - Wide / expanded (>= 760px): dual-pane command center (Board or embedded Member Detail on the left, live Members + Tasks rail on the right).
 * - Narrow side dock (< 760px): clean single-column 3-segment switcher (Board / Members / Tasks) with full-height Member Detail drill-down.
 */
export function TeamPanel(input: TeamPanelProps) {
  const props: TeamPanelProps = { ...input, team: { ...input.team, members: members(input.team), tasks: tasks(input.team) } };
  const { team, usage, actions, memberName } = props;
  const panelRef = useRef<HTMLElement>(null);
  const isWide = useWidePanel(panelRef);
  const [view, setView] = useState<View>("board");
  const [confirmDissolve, setConfirmDissolve] = useState(false);
  const [highlightedTaskKey, setHighlightedTaskKey] = useState<string>();
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const spinTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => {
    clearTimeout(highlightTimer.current);
    clearTimeout(spinTimer.current);
  }, []);

  const handleManualRefresh = useCallback(() => {
    setRefreshing(true);
    setRefreshNonce((previous) => previous + 1);
    const startedAt = Date.now();
    void Promise.resolve(actions.refresh()).finally(() => {
      const remaining = Math.max(0, 360 - (Date.now() - startedAt));
      clearTimeout(spinTimer.current);
      spinTimer.current = setTimeout(() => setRefreshing(false), remaining);
    });
  }, [actions]);

  const allMembers = members(team);
  const allTasks = tasks(team);
  const member = memberName ? allMembers.find((entry) => entry.name === memberName) : undefined;
  const approvalsBySession = useMemo(() => {
    const counts = new Map<string, number>();
    for (const request of props.approvals) counts.set(request.sessionId, (counts.get(request.sessionId) ?? 0) + 1);
    return counts;
  }, [props.approvals]);

  const activeMembers = allMembers.filter((entry) => entry.status === "active");
  const busyMembers = activeMembers.filter((entry) => entry.runStatus && ACTIVE_RUN.has(entry.runStatus)).length;
  const totalApprovals = useMemo(
    () => activeMembers.reduce((sum, entry) => sum + (approvalsBySession.get(entry.sessionId) ?? 0), 0),
    [activeMembers, approvalsBySession],
  );
  const doneTasks = allTasks.filter((task) => task.status === "done").length;
  const closed = team.status === "dissolving" || team.status === "dissolved";

  const usageSummary = useMemo(() => totalUsage(usage), [usage]);
  const tokensLabel = formatTokens(usageSummary.total, usageSummary.incomplete);
  const defaultModelDisplay = useMemo(
    () => resolveModelLabel(team.runSettings.modelId, props.models),
    [team.runSettings.modelId, props.models],
  );

  const handleFocusTask = useCallback((taskKey: string) => {
    if (!isWide) {
      if (member) props.onBack();
      setView("tasks");
    }
    setHighlightedTaskKey(taskKey);
    clearTimeout(highlightTimer.current);
    highlightTimer.current = setTimeout(() => setHighlightedTaskKey(undefined), 1400);
  }, [isWide, member, props]);

  const statusTone: StatusTone = team.status === "active" ? "success" : team.status === "paused" ? "neutral" : "danger";
  const statusLabel = team.status === "active" && isWide && busyMembers > 0
    ? `${busyMembers}/${activeMembers.length} 运行中`
    : TEAM_STATUS_LABELS[team.status];

  const statusTooltipRows: TooltipRow[] = [
    ...(team.status === "active"
      ? [{ label: "成员", value: `${busyMembers} / ${activeMembers.length} 运行中`, tone: busyMembers > 0 ? ("running" as const) : ("muted" as const) }]
      : [{ label: "成员", value: `${activeMembers.length} 名` }]),
    { label: "自动续跑", value: team.autoWakeLead ? "开启" : "关闭" },
    ...(totalApprovals > 0 ? [{ label: "待审批", value: `${totalApprovals} 项`, tone: "waiting" as const }] : []),
    ...(tokensLabel ? [{ label: "累计用量", value: tokensLabel }] : []),
  ];
  const statusTooltip = (
    <TeamTooltipCard
      title={TEAM_STATUS_LABELS[team.status]}
      badge={`${activeMembers.length} 名成员`}
      rows={statusTooltipRows}
    />
  );

  const headerMenuItems: DropdownMenuItem[] = [
    {
      key: "auto-wake",
      label: "自动续跑",
      checked: team.autoWakeLead,
      disabled: actions.busy,
      onClick: () => void actions.execute("team/updateSettings", { teamId: team.id, autoWakeLead: !team.autoWakeLead }),
    },
    {
      key: "stop-members",
      label: busyMembers > 0 ? `停止运行中成员 (${busyMembers})` : "停止运行中成员",
      disabled: actions.busy || busyMembers === 0,
      onClick: () => void actions.execute("team/stopMembers", { teamId: team.id }),
    },
    {
      key: "dissolve",
      label: "解散团队…",
      separator: true,
      danger: true,
      disabled: actions.busy,
      onClick: () => setConfirmDissolve(true),
    },
  ];

  const showTeamHeader = isWide || !member;

  return (
    <section
      ref={panelRef}
      className={`team-panel${isWide ? " team-panel--wide" : ""}`}
      aria-label="团队"
    >
      {showTeamHeader && (
        <header className="team-panel__header">
          <div className="team-panel__title-row">
            <Tooltip content={team.objective} placement="bottom">
              <h2 className="team-panel__objective">{team.objective}</h2>
            </Tooltip>
          </div>

          <div className="team-panel__controls">
            <Tooltip content={statusTooltip} placement="bottom" className="team-detail-tooltip">
              <span className="team-panel__status-wrap">
                <StatusBadge
                  tone={statusTone}
                  dot
                  className={`team-panel__status team-panel__status--${team.status}${team.status === "active" && busyMembers > 0 ? " team-panel__status--live" : ""}`}
                >
                  {statusLabel}
                </StatusBadge>
              </span>
            </Tooltip>

            {!closed && !team.autoWakeLead && (
              <Tooltip content="已关闭自动续跑，成员汇报后不会自动唤醒 Lead" placement="bottom">
                <span className="team-panel__manual-chip">手动</span>
              </Tooltip>
            )}

            <Tooltip content="刷新团队进度" placement="bottom">
              <button
                type="button"
                className="icon-button team-panel__icon-btn"
                aria-label="刷新"
                disabled={refreshing}
                onClick={handleManualRefresh}
              >
                <RefreshIcon size={13.5} spinning={refreshing} className="team-panel__refresh-icon" />
              </button>
            </Tooltip>

            {!closed && (
              <>
                <Tooltip content={team.status === "paused" ? "继续团队" : "暂停团队"} placement="bottom">
                  <button
                    type="button"
                    className="icon-button team-panel__icon-btn"
                    aria-label={team.status === "paused" ? "继续" : "暂停"}
                    disabled={actions.busy}
                    onClick={() => void actions.execute("team/setStatus", { teamId: team.id, status: team.status === "paused" ? "active" : "paused" })}
                  >
                    {team.status === "paused" ? <GoalPlayIcon /> : <GoalPauseIcon />}
                  </button>
                </Tooltip>
                <DropdownMenu
                  align="right"
                  label="更多团队操作"
                  title="更多团队操作"
                  triggerAriaLabel="更多团队操作"
                  triggerClassName="team-panel__icon-btn"
                  trigger={<MoreHorizontalIcon />}
                  items={headerMenuItems}
                />
              </>
            )}
          </div>
        </header>
      )}

      {!isWide && !member && (
        <div className="team-panel__nav">
          <div className="team-panel__views" role="tablist" aria-label="团队视图">
            <button
              type="button"
              role="tab"
              aria-selected={view === "board"}
              className={`team-panel__view${view === "board" ? " team-panel__view--active" : ""}`}
              onClick={() => setView("board")}
            >
              看板
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "members"}
              className={`team-panel__view${view === "members" ? " team-panel__view--active" : ""}`}
              onClick={() => setView("members")}
            >
              <span>成员</span>
              <span className="team-panel__view-count">{activeMembers.length}</span>
              {totalApprovals > 0 && <span className="team-panel__view-alert" aria-hidden="true" />}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "tasks"}
              className={`team-panel__view${view === "tasks" ? " team-panel__view--active" : ""}`}
              onClick={() => setView("tasks")}
            >
              <span>任务</span>
              {allTasks.length > 0 && <span className="team-panel__view-count">{doneTasks}/{allTasks.length}</span>}
            </button>
          </div>
        </div>
      )}

      {actions.error && showTeamHeader && <AlertBanner tone="error">{actions.error}</AlertBanner>}

      {isWide ? (
        <div className="team-panel__split">
          <div className="team-panel__main">
            {member ? (
              <TeamMemberDetail
                {...props}
                member={member}
                embedded
                defaultModelDisplay={defaultModelDisplay}
                pendingApprovals={approvalsBySession.get(member.sessionId) ?? 0}
                memberUsage={usage.find((value) => value.member === member.name)}
                refreshNonce={refreshNonce}
                refreshing={refreshing}
                onRefresh={handleManualRefresh}
              />
            ) : (
              <TeamBoard team={team} actions={actions} refreshNonce={refreshNonce} onFocusTask={handleFocusTask} />
            )}
          </div>
          <aside className="team-panel__rail" aria-label="成员与任务概览">
            <section className="team-rail__section" aria-label="团队成员">
              <header className="team-rail__section-head">
                <h3>成员</h3>
              </header>
              <TeamMembers
                team={team}
                usage={usage}
                approvals={approvalsBySession}
                models={props.models}
                defaultModelDisplay={defaultModelDisplay}
                selectedMemberName={member?.name}
                compact
                onOpen={props.onOpenMember}
              />
            </section>
            <section className="team-rail__section" aria-label="团队任务">
              <TeamTasks
                team={team}
                actions={actions}
                compact
                highlightedTaskKey={highlightedTaskKey}
                onOpenMember={props.onOpenMember}
              />
            </section>
          </aside>
        </div>
      ) : member ? (
        <TeamMemberDetail
          {...props}
          member={member}
          embedded={false}
          defaultModelDisplay={defaultModelDisplay}
          pendingApprovals={approvalsBySession.get(member.sessionId) ?? 0}
          memberUsage={usage.find((value) => value.member === member.name)}
          refreshNonce={refreshNonce}
          refreshing={refreshing}
          onRefresh={handleManualRefresh}
        />
      ) : (
        <>
          {view === "board" && <TeamBoard team={team} actions={actions} refreshNonce={refreshNonce} onFocusTask={handleFocusTask} />}
          {view === "members" && (
            <div className="team-scroll-view">
              <TeamMembers
                team={team}
                usage={usage}
                approvals={approvalsBySession}
                models={props.models}
                defaultModelDisplay={defaultModelDisplay}
                compact={false}
                onOpen={props.onOpenMember}
              />
            </div>
          )}
          {view === "tasks" && (
            <div className="team-scroll-view">
              <TeamTasks
                team={team}
                actions={actions}
                compact={false}
                highlightedTaskKey={highlightedTaskKey}
                onOpenMember={props.onOpenMember}
              />
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmDissolve}
        title="解散团队"
        isDestructive
        confirmLabel="解散"
        busy={actions.busy}
        description="停止所有成员，删除成员的 Worktree。未整合的变更会丢失。"
        onCancel={() => setConfirmDissolve(false)}
        onConfirm={() => { setConfirmDissolve(false); void actions.execute("team/setStatus", { teamId: team.id, status: "dissolving" }); }}
      />
    </section>
  );
}

// Board ------------------------------------------------------------------------------

type BoardTimelineEntry =
  | { kind: "time-divider"; id: string; timestamp: number }
  | { kind: "message"; message: TeamMessageView }
  | { kind: "system-group"; id: string; messages: TeamMessageView[] };

function buildBoardTimeline(list: readonly TeamMessageView[], showSystem: boolean): BoardTimelineEntry[] {
  const visible = showSystem ? list : list.filter((item) => item.authorKind !== "system");
  const entries: BoardTimelineEntry[] = [];
  let lastDividerAt: number | undefined;
  let index = 0;

  while (index < visible.length) {
    const current = visible[index]!;
    if (lastDividerAt === undefined || current.createdAt - lastDividerAt >= TIME_DIVIDER_GAP_MS) {
      entries.push({ kind: "time-divider", id: `time-${current.id}`, timestamp: current.createdAt });
      lastDividerAt = current.createdAt;
    }

    if (current.authorKind === "system") {
      const run: TeamMessageView[] = [current];
      while (index + 1 < visible.length && visible[index + 1]!.authorKind === "system") {
        index += 1;
        run.push(visible[index]!);
      }
      if (run.length >= 2) {
        entries.push({ kind: "system-group", id: `sys-${run[0]!.id}`, messages: run });
      } else {
        entries.push({ kind: "message", message: current });
      }
    } else {
      entries.push({ kind: "message", message: current });
    }
    index += 1;
  }

  return entries;
}

function summarizeSystemRun(run: readonly TeamMessageView[]): string {
  let joined = 0;
  let assigned = 0;
  for (const item of run) {
    if (item.task || /assigned to you/i.test(item.content)) assigned += 1;
    else if (/joined the team/i.test(item.content)) joined += 1;
  }
  const parts: string[] = [];
  if (joined > 0) parts.push(`${joined} 名成员加入`);
  if (assigned > 0) parts.push(`分派 ${assigned} 项任务`);
  const rest = run.length - joined - assigned;
  if (rest > 0) parts.push(`${rest} 条通知`);
  return parts.length > 0 ? parts.join("，") : `${run.length} 条系统动态`;
}

function formatMessageTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function TeamBoard({ team, actions, refreshNonce, onFocusTask }: {
  team: TeamView;
  actions: TeamActions;
  refreshNonce: number;
  onFocusTask: (taskKey: string) => void;
}) {
  const [messages, setMessages] = useState<TeamMessageView[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState("");
  const [memberFilter, setMemberFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState<"" | "board" | "direct">("");
  const [showSystem, setShowSystem] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<ReadonlySet<string>>(() => new Set());
  const cursor = useRef(0);
  const loading = useRef(false);
  const pending = useRef(false);
  const pendingReset = useRef(false);
  const generation = useRef(0);
  const loadRef = useRef<(reset: boolean) => Promise<void>>(async () => {});

  const load = useCallback(async (reset: boolean) => {
    if (reset) {
      // A new query scope invalidates any page still in flight before it can apply or move the cursor.
      generation.current += 1;
      cursor.current = 0;
    }
    if (loading.current) {
      pending.current = true;
      pendingReset.current ||= reset;
      return;
    }
    loading.current = true;
    const token = generation.current;
    try {
      const collected: TeamMessageView[] = [];
      let more = false;
      for (let page = 0; page < MESSAGE_PAGES; page++) {
        const result = await window.eidosRuntime.team("team/messages", {
          teamId: team.id, after: cursor.current, limit: MESSAGE_PAGE,
          ...(memberFilter ? { member: memberFilter } : {}), ...(channelFilter ? { channel: channelFilter } : {}),
        });
        if (token !== generation.current) return;
        collected.push(...(result.messages ?? []));
        cursor.current = result.nextCursor ?? cursor.current;
        more = result.hasMore ?? false;
        if (!more) break;
      }
      setMessages((previous) => {
        const seen = new Set(reset ? [] : previous.map((message) => message.id));
        return [...(reset ? [] : previous), ...collected.filter((message) => !seen.has(message.id))];
      });
      if (more) setTruncated(true);
      else if (reset) setTruncated(false);
      setError("");
    } catch (cause) {
      if (token === generation.current) setError(userFacingError(cause));
    } finally {
      loading.current = false;
      if (pending.current) {
        const resetNext = pendingReset.current;
        pending.current = false;
        pendingReset.current = false;
        // The latest load carries the current filters; a closure from an older render would not.
        void loadRef.current(resetNext);
      }
    }
  }, [team.id, memberFilter, channelFilter]);
  loadRef.current = load;

  const syncSignal = `${actions.revision}:${refreshNonce}:${team.version}`;
  const lastSyncRef = useRef(syncSignal);

  useEffect(() => {
    lastSyncRef.current = syncSignal;
    void load(true);
    // Reset only when query scope (`load`) changes; incremental updates are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  useEffect(() => {
    if (syncSignal === lastSyncRef.current) return;
    lastSyncRef.current = syncSignal;
    void load(false);
  }, [syncSignal, load]);

  useEffect(() => window.eidosRuntime.onNotification((notice) => {
    if (notice.method === "team/updated" && "sessionId" in notice.params && notice.params.sessionId === team.leadSessionId) {
      void load(false);
    }
  }), [team.leadSessionId, load]);

  const memberRoles = useMemo(
    () => new Map(members(team).map((entry) => [entry.name, entry.role])),
    [team],
  );
  const taskMap = useMemo(
    () => new Map(tasks(team).map((entry) => [entry.key, entry])),
    [team],
  );
  const systemCount = useMemo(
    () => messages.filter((message) => message.authorKind === "system").length,
    [messages],
  );
  const effectiveShowSystem = showSystem && channelFilter !== "direct";
  const timeline = useMemo(
    () => buildBoardTimeline(messages, effectiveShowSystem),
    [messages, effectiveShowSystem],
  );
  const memberFilterItems: DropdownMenuItem[] = useMemo(() => [
    { key: "__all__", label: "全部成员", checked: memberFilter === "", onClick: () => setMemberFilter("") },
    ...members(team).map((entry) => ({
      key: entry.name,
      label: entry.name,
      checked: memberFilter === entry.name,
      onClick: () => setMemberFilter(entry.name),
    })),
  ], [team, memberFilter]);

  const toggleGroup = (id: string) => setExpandedGroups((previous) => {
    const next = new Set(previous);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className="team-board">
      <div className="team-board__filters">
        <div className="team-board__channel-pills" role="group" aria-label="按频道筛选">
          {([
            { id: "", label: "全部" },
            { id: "board", label: "看板" },
            { id: "direct", label: "私聊" },
          ] as const).map((entry) => (
            <button
              key={entry.id || "all"}
              type="button"
              aria-pressed={channelFilter === entry.id}
              className={`team-filter-chip${channelFilter === entry.id ? " team-filter-chip--active" : ""}`}
              onClick={() => setChannelFilter(entry.id)}
            >
              {entry.id === "direct" && <LockIcon size={10} />}
              <span>{entry.label}</span>
            </button>
          ))}
        </div>

        <div className="team-board__right-filters">
          {systemCount > 0 && channelFilter !== "direct" && (
            <Tooltip content={showSystem ? "隐藏系统入队与分派日志" : `显示系统日志（${systemCount} 条）`}>
              <button
                type="button"
                aria-pressed={showSystem}
                aria-label={`系统日志（${systemCount} 条）`}
                className={`team-filter-chip team-filter-chip--system${showSystem ? " team-filter-chip--active" : ""}`}
                onClick={() => setShowSystem((value) => !value)}
              >
                <HistoryIcon width={12} height={12} />
                <span>系统</span>
                <span className="team-filter-chip__count">{systemCount}</span>
              </button>
            </Tooltip>
          )}

          <div className="team-board__member-filter">
            {memberFilter ? (
              <span className="team-filter-chip team-filter-chip--active team-filter-chip--removable">
                <span>@{memberFilter}</span>
                <button
                  type="button"
                  className="team-filter-chip__clear"
                  aria-label="清除成员筛选"
                  onClick={() => setMemberFilter("")}
                >
                  <CloseIcon width={10} height={10} />
                </button>
              </span>
            ) : (
              <DropdownMenu
                align="right"
                label="按成员筛选"
                triggerAriaLabel="按成员筛选"
                triggerClassName="team-filter-dropdown-trigger"
                trigger={<><span>成员</span><ChevronDownIcon width={11} height={11} /></>}
                items={memberFilterItems}
              />
            )}
          </div>
        </div>
      </div>

      {error && <AlertBanner tone="error">{error}</AlertBanner>}

      {timeline.length === 0 && !error ? (
        !effectiveShowSystem && systemCount > 0 && channelFilter !== "direct" ? (
          <EmptyState
            className="team-empty"
            icon={<TeamIcon />}
            title="等待成员发言"
            action={
              <Button size="small" variant="ghost" onClick={() => setShowSystem(true)}>
                查看 {systemCount} 条系统动态
              </Button>
            }
          />
        ) : (
          <EmptyState className="team-empty" icon={<TeamIcon />} title="暂无消息" />
        )
      ) : (
        <ol className="team-board__messages" aria-label="团队消息">
          {truncated && <li className="team-board__more">更多消息未加载</li>}
          {timeline.map((entry) => {
            if (entry.kind === "time-divider") {
              return (
                <li key={entry.id} className="team-time-divider" aria-label={formatMessageTime(entry.timestamp)}>
                  <time dateTime={new Date(entry.timestamp).toISOString()}>{formatMessageTime(entry.timestamp)}</time>
                </li>
              );
            }
            if (entry.kind === "system-group") {
              const open = expandedGroups.has(entry.id);
              return (
                <li key={entry.id} className="team-system-group">
                  <button
                    type="button"
                    className="team-system-group__summary"
                    aria-expanded={open}
                    onClick={() => toggleGroup(entry.id)}
                  >
                    <span className="team-system-dot" aria-hidden="true" />
                    <span className="team-system-group__label">{summarizeSystemRun(entry.messages)}</span>
                    <ChevronDownIcon className={`user-message-expand-icon${open ? " is-expanded" : ""}`} />
                  </button>
                  {open && (
                    <ul className="team-rail-body team-rail-body--system team-system-group__list">
                      {entry.messages.map((item) => (
                        <TeamMessage
                          key={item.id}
                          message={item}
                          role={memberRoles.get(item.author)}
                          task={item.task ? taskMap.get(item.task) : undefined}
                          onFocusTask={onFocusTask}
                          compactSystem
                        />
                      ))}
                    </ul>
                  )}
                </li>
              );
            }
            const item = entry.message;
            return (
              <TeamMessage
                key={item.id}
                message={item}
                role={memberRoles.get(item.author)}
                task={item.task ? taskMap.get(item.task) : undefined}
                onFocusTask={onFocusTask}
              />
            );
          })}
        </ol>
      )}
      <TeamPostComposer team={team} actions={actions} />
    </div>
  );
}

function TeamMessage({ message, role, task, onFocusTask, compactSystem = false }: {
  message: TeamMessageView;
  role?: string | undefined;
  task?: TeamTaskView | undefined;
  onFocusTask: (taskKey: string) => void;
  compactSystem?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const node = bodyRef.current;
    if (!node || message.authorKind === "system" || message.authorKind === "user") {
      setOverflowing(false);
      return;
    }
    const measure = () => {
      setOverflowing(node.scrollHeight > MESSAGE_COLLAPSE_PX + 12);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [message.content, message.authorKind]);

  const recipients = message.toAll ? ["全体"] : (message.recipients ?? []).map((entry) => (entry === "all" ? "全体" : entry));
  const timeText = formatMessageTime(message.createdAt);
  const isoTime = new Date(message.createdAt).toISOString();

  if (message.authorKind === "system") {
    return (
      <li className={`team-message team-message--system${compactSystem ? " team-message--system-nested" : ""}`}>
        {!compactSystem && <span className="team-system-dot" aria-hidden="true" />}
        <span className="team-system-line">
          {recipients.length > 0 && <span className="team-system-line__target">→ {recipients.join(", ")}</span>}
          {message.task && (
            <Tooltip content={task ? `定位任务：${task.title}` : message.task}>
              <button type="button" className="team-message__task" onClick={() => onFocusTask(message.task!)}>
                #{message.task}
              </button>
            </Tooltip>
          )}
          <span className="team-system-line__body">{message.content}</span>
        </span>
        <time className="team-message__time" dateTime={isoTime}>{timeText}</time>
      </li>
    );
  }

  const isUser = message.authorKind === "user";
  const isDirect = message.channel === "direct";

  if (isUser) {
    return (
      <li className={`team-message team-message--user${isDirect ? " team-message--direct" : ""}`}>
        <div className="team-user-bubble">
          <div className="team-user-bubble__head">
            <span className="team-user-bubble__target">
              {recipients.length > 0 ? `发给 ${recipients.join(", ")}` : "你"}
              {isDirect && (
                <Tooltip content="私聊消息">
                  <span className="team-message__direct-icon" aria-label="私聊"><LockIcon size={10.5} /></span>
                </Tooltip>
              )}
            </span>
            <time className="team-message__time" dateTime={isoTime}>{timeText}</time>
          </div>
          <MarkdownContent content={message.content} className="team-message__markdown" />
          {message.task && (
            <div className="team-message__foot">
              <Tooltip content={task ? `定位任务：${task.title}` : message.task}>
                <button type="button" className="team-message__task" onClick={() => onFocusTask(message.task!)}>
                  #{message.task}
                </button>
              </Tooltip>
            </div>
          )}
        </div>
      </li>
    );
  }

  const avatarTone = role === "verifier" ? "verifier" : "member";

  return (
    <li className={`team-message team-message--${message.authorKind}${isDirect ? " team-message--direct" : ""}`}>
      <div className="team-message__head">
        <span className={`team-message__avatar team-message__avatar--${avatarTone}`} aria-hidden="true">
          {(message.author[0] ?? "?").toUpperCase()}
        </span>
        <span className="team-message__author">{message.author}</span>
        {recipients.length > 0 && <span className="team-message__to">→ {recipients.join(", ")}</span>}
        {isDirect && (
          <Tooltip content="私聊消息">
            <span className="team-message__direct-icon" aria-label="私聊"><LockIcon size={11} /></span>
          </Tooltip>
        )}
        <time className="team-message__time" dateTime={isoTime}>{timeText}</time>
      </div>

      <div className={`team-rail-body${isDirect ? " team-rail-body--direct" : ""}`}>
        <div ref={bodyRef} className={`team-message__collapsible${overflowing && !expanded ? " is-collapsed" : ""}`}>
          <MarkdownContent content={message.content} className="team-message__markdown" />
          {overflowing && !expanded && <div className="team-message__fade-mask" aria-hidden="true" />}
        </div>

        {(overflowing || message.task) && (
          <div className="team-message__foot">
            {overflowing && (
              <button
                type="button"
                className="user-message-expand-btn team-message__expand-btn"
                aria-expanded={expanded}
                onClick={() => setExpanded((value) => !value)}
              >
                <span>{expanded ? "收起" : "显示更多"}</span>
                <ChevronDownIcon className={`user-message-expand-icon${expanded ? " is-expanded" : ""}`} />
              </button>
            )}
            {message.task && (
              <Tooltip content={task ? `定位任务：${task.title}` : message.task}>
                <button type="button" className="team-message__task" onClick={() => onFocusTask(message.task!)}>
                  #{message.task}
                </button>
              </Tooltip>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

function TeamPostComposer({ team, actions, member }: { team: TeamView; actions: TeamActions; member?: TeamMemberView }) {
  const [text, setText] = useState("");
  const [recipient, setRecipient] = useState(member?.name ?? "lead");
  const [direct, setDirect] = useState(Boolean(member));
  const closed = team.status === "dissolving" || team.status === "dissolved";
  const target = member?.name ?? recipient;
  const isDirect = target !== "all" && (member ? true : direct);
  const canSend = text.trim().length > 0 && !actions.busy && !closed;
  const targetDisplay = target === "all" ? "全体" : target;

  const send = async () => {
    const value = text.trim();
    if (!value || actions.busy || closed) return;
    if (await actions.execute("team/post", { teamId: team.id, message: { to: [target], channel: isDirect ? "direct" : "board", text: value } })) {
      setText("");
    }
  };

  return (
    <form className="team-post" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <div className="team-post__capsule">
        <textarea
          rows={2}
          value={text}
          disabled={closed}
          aria-label={`发给 ${targetDisplay}`}
          placeholder={member ? `给 ${member.name} 发私聊…` : `发给 ${targetDisplay}…`}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
        />
        <div className="team-post__bar">
          <div className="team-post__controls">
            {member ? (
              <Tooltip content={`私聊 ${member.name}`}>
                <span className="team-post__lock-hint" aria-label={`私聊 ${member.name}`}>
                  <LockIcon size={12} />
                </span>
              </Tooltip>
            ) : (
              <>
                <span className="team-post__recipient-pill">
                  <span className="team-post__at" aria-hidden="true">@</span>
                  <Select
                    aria-label="发给"
                    className="team-post__select"
                    value={recipient}
                    disabled={closed}
                    onChange={(event) => {
                      setRecipient(event.target.value);
                      if (event.target.value === "all") setDirect(false);
                    }}
                  >
                    <option value="lead">lead</option>
                    {members(team).filter((entry) => entry.status === "active").map((entry) => (
                      <option key={entry.name} value={entry.name}>{entry.name}</option>
                    ))}
                    <option value="all">全体</option>
                  </Select>
                </span>
                {recipient !== "all" && (
                  <Tooltip content={isDirect ? "私聊：只发给收件人（点击切回公开看板）" : "公开看板：全员可见（点击切换为私聊）"}>
                    <button
                      type="button"
                      className={`team-post__direct-btn${isDirect ? " team-post__direct-btn--active" : ""}`}
                      aria-pressed={isDirect}
                      aria-label="私聊"
                      disabled={closed}
                      onClick={() => setDirect((value) => !value)}
                    >
                      <LockIcon size={11} />
                      {isDirect && <span>私聊</span>}
                    </button>
                  </Tooltip>
                )}
              </>
            )}
          </div>
          <Tooltip content="发送">
            <Button
              type="submit"
              variant="primary"
              size="medium"
              className={`composer-submit-btn team-post__submit${!canSend ? " composer-submit-btn--empty" : ""}`}
              disabled={!canSend}
              aria-label="发送"
              icon={<UpArrowIcon />}
            >
              <span className="sr-only">发送</span>
            </Button>
          </Tooltip>
        </div>
      </div>
    </form>
  );
}

// Members ------------------------------------------------------------------------------

function TeamMembers({
  team,
  usage,
  approvals,
  models,
  defaultModelDisplay,
  selectedMemberName,
  compact,
  onOpen,
}: {
  team: TeamView;
  usage: readonly TeamUsage[];
  approvals: ReadonlyMap<string, number>;
  models?: readonly ModelOption[] | undefined;
  defaultModelDisplay?: string | undefined;
  selectedMemberName?: string | undefined;
  compact: boolean;
  onOpen: (name: string) => void;
}) {
  const allTasks = tasks(team);

  return (
    <ul className={`team-members${compact ? " team-members--compact" : ""}`} aria-label="团队成员">
      {members(team).map((member) => {
        const busy = member.runStatus ? ACTIVE_RUN.has(member.runStatus) : false;
        const pending = approvals.get(member.sessionId) ?? 0;
        const value = usage.find((entry) => entry.member === member.name);
        const total = value ? (value.inputTokens ?? 0) + (value.outputTokens ?? 0) : 0;
        const incomplete = value?.incomplete ?? false;
        const tokensLabel = formatTokens(total, incomplete);
        const modelDisplay = resolveModelLabel(member.modelId, models);
        const isHeterogeneousModel = Boolean(modelDisplay && defaultModelDisplay && modelDisplay !== defaultModelDisplay);
        const stateLabel = memberState(member);
        const stateTone = memberStateTone(member);
        const roleLabel = MEMBER_ROLE_LABELS[member.role] ?? member.role;
        const currentTask = activeOwnedTask(allTasks, member.name);
        const selected = selectedMemberName === member.name;

        const tooltipRows: TooltipRow[] = [
          {
            label: "状态",
            value: stateLabel,
            tone: stateTone === "running" ? "running" : stateTone === "waiting" ? "waiting" : "muted",
          },
          ...(modelDisplay ? [{ label: "模型", value: modelDisplay }] : []),
          { label: "工作区", value: member.workspaceMode === "worktree" ? "独立 Worktree" : "共享工作区" },
          ...(tokensLabel ? [{ label: "用量", value: tokensLabel }] : []),
          ...(pending > 0 ? [{ label: "待审批", value: `${pending} 项`, tone: "waiting" as const }] : []),
        ];
        const tooltipContent = (
          <TeamTooltipCard title={member.name} badge={roleLabel} rows={tooltipRows} />
        );

        return (
          <li key={member.id}>
            <Tooltip content={tooltipContent} placement={compact ? "left" : "top"} className="team-detail-tooltip">
              <button
                type="button"
                aria-label={`${member.name} · ${roleLabel} · ${stateLabel}${pending > 0 ? ` · ${pending} 个待审批` : ""}`}
                className={`team-member${busy ? " team-member--busy" : ""}${selected ? " team-member--selected" : ""}${member.status === "removed" ? " team-member--removed" : ""}`}
                onClick={() => onOpen(member.name)}
              >
                <span className="team-member__top">
                  <span className="team-member__identity">
                    <span className={memberStatusDotClass(member)} aria-hidden="true" />
                    <span className="team-member__name">{member.name}</span>
                    <span className={memberRolePillClass(member.role)}>{roleLabel}</span>
                    {member.workspaceMode === "worktree" && (
                      <span className="team-member__worktree" aria-label="独立 Worktree">
                        <GitBranchIcon width={12} height={12} />
                      </span>
                    )}
                  </span>
                  <span className="team-member__aside">
                    {pending > 0 && (
                      <span className="agent-approval-badge" aria-label={`${pending} 个待审批`}>{pending}</span>
                    )}
                    <span className={`team-member__state team-member__state--${stateTone}`}>{stateLabel}</span>
                    <span className="team-member__chevron" aria-hidden="true">
                      <ChevronRightIcon width={12} height={12} />
                    </span>
                  </span>
                </span>
                {currentTask && (
                  <span className="team-member__activity">{currentTask.title}</span>
                )}
                {isHeterogeneousModel && (
                  <span className="team-member__model">{modelDisplay}</span>
                )}
              </button>
            </Tooltip>
          </li>
        );
      })}
    </ul>
  );
}

function TeamMemberDetail(props: TeamPanelProps & {
  member: TeamMemberView;
  embedded: boolean;
  defaultModelDisplay?: string | undefined;
  pendingApprovals: number;
  memberUsage: TeamUsage | undefined;
  refreshNonce: number;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const { team, member, actions, embedded, defaultModelDisplay, refreshNonce, refreshing, onRefresh } = props;
  const [confirmRemove, setConfirmRemove] = useState<"keep" | "discard">();
  const busy = member.runStatus ? ACTIVE_RUN.has(member.runStatus) : false;
  const own = props.memberUsage;
  const total = own ? (own.inputTokens ?? 0) + (own.outputTokens ?? 0) : 0;
  const incomplete = own?.incomplete ?? false;
  const tokensLabel = formatTokens(total, incomplete);
  const open = team.status !== "dissolving" && team.status !== "dissolved" && member.status === "active";
  const roleLabel = MEMBER_ROLE_LABELS[member.role] ?? member.role;
  const stateLabel = memberState(member);
  const stateTone = memberStateTone(member);
  const modelDisplay = resolveModelLabel(member.modelId, props.models);
  const isHeterogeneousModel = Boolean(modelDisplay && defaultModelDisplay && modelDisplay !== defaultModelDisplay);
  const currentTask = activeOwnedTask(tasks(team), member.name);

  const identityTooltipRows: TooltipRow[] = [
    {
      label: "状态",
      value: stateLabel,
      tone: stateTone === "running" ? "running" : stateTone === "waiting" ? "waiting" : "muted",
    },
    ...(modelDisplay ? [{ label: "模型", value: modelDisplay }] : []),
    { label: "工作区", value: member.workspaceMode === "worktree" ? "独立 Worktree" : "共享工作区" },
    ...(tokensLabel ? [{ label: "用量", value: tokensLabel }] : []),
    ...(currentTask ? [{ label: "当前任务", value: currentTask.title }] : []),
  ];
  const identityTooltip = (
    <TeamTooltipCard title={member.name} badge={roleLabel} rows={identityTooltipRows} />
  );

  const remove = async (discardChanges: boolean) => {
    setConfirmRemove(undefined);
    if (await actions.execute("team/removeMember", { teamId: team.id, name: member.name, discardChanges })) props.onBack();
  };

  return (
    <article className="team-member-detail" aria-label={`成员 ${member.name}`}>
      <header className="team-member-detail__header">
        <div className="team-member-detail__left">
          <button
            type="button"
            className="agent-detail-back-btn"
            aria-label={embedded ? "返回看板" : "返回团队"}
            onClick={props.onBack}
          >
            <ChevronLeftIcon />
            <span>{embedded ? "看板" : "成员"}</span>
          </button>

          <Tooltip content={identityTooltip} placement="bottom" className="team-detail-tooltip">
            <div className="team-member-detail__identity">
              <span className={memberStatusDotClass(member)} aria-hidden="true" />
              <h2>{member.name}</h2>
              <span className={memberRolePillClass(member.role)}>{roleLabel}</span>
              <span className={`team-member-detail__state team-member-detail__state--${stateTone}`}>{stateLabel}</span>
              {member.workspaceMode === "worktree" && (
                <span className="team-member__worktree" aria-label="独立 Worktree">
                  <GitBranchIcon width={12} height={12} />
                </span>
              )}
              {isHeterogeneousModel && <span className="team-member-detail__model">{modelDisplay}</span>}
            </div>
          </Tooltip>
        </div>

        <div className="team-member-detail__actions">
          {!embedded && (
            <Tooltip content="刷新成员进度" placement="bottom">
              <button
                type="button"
                className="icon-button team-panel__icon-btn"
                aria-label="刷新"
                disabled={refreshing}
                onClick={onRefresh}
              >
                <RefreshIcon size={13.5} spinning={refreshing} className="team-panel__refresh-icon" />
              </button>
            </Tooltip>
          )}
          {busy && member.runId && (
            <button
              type="button"
              className="agent-stop-button"
              onClick={() => props.onCancelRun(member.runId!, member.sessionId)}
              aria-label="停止"
              title="停止该成员当前运行"
            >
              <svg viewBox="0 0 16 16" width="10" height="10" fill="currentColor" aria-hidden="true" className="agent-stop-button__icon">
                <rect x="3" y="3" width="10" height="10" rx="2" />
              </svg>
              <span className="agent-stop-button__text">停止</span>
            </button>
          )}
          {open && (
            <DropdownMenu
              align="right"
              label="成员操作"
              title="成员操作"
              triggerAriaLabel="成员操作"
              triggerClassName="team-panel__icon-btn"
              trigger={<MoreHorizontalIcon />}
              items={[
                {
                  key: "remove",
                  label: "移除成员…",
                  danger: true,
                  disabled: actions.busy,
                  onClick: () => setConfirmRemove("keep"),
                },
              ]}
            />
          )}
        </div>
      </header>

      {actions.error && (
        <AlertBanner
          tone="error"
          action={actions.error === RUNTIME_ERROR_MESSAGES.TEAM_MEMBER_HAS_CHANGES && open
            ? <Button size="small" variant="danger" onClick={() => setConfirmRemove("discard")}>放弃变更并移除</Button>
            : undefined}
        >
          {actions.error}
        </AlertBanner>
      )}

      {member.workspaceMode === "worktree" && !busy && (
        <TeamMemberChangesView team={team} member={member} refreshNonce={refreshNonce} />
      )}

      {member.runId ? (
        <AgentTranscript
          agent={{
            id: member.id, sessionId: member.sessionId, runId: member.runId,
            status: member.runStatus ?? "succeeded", createdAt: member.createdAt,
            task: member.brief, taskName: member.name,
          }}
          approvals={props.approvals}
          {...(props.respondingApprovalIds ? { respondingApprovalIds: props.respondingApprovalIds } : {})}
          {...(props.respondingKindByApprovalId ? { respondingKindByApprovalId: props.respondingKindByApprovalId } : {})}
          {...(props.expiredApprovalIds ? { expiredApprovalIds: props.expiredApprovalIds } : {})}
          {...(props.errorsByApprovalId ? { errorsByApprovalId: props.errorsByApprovalId } : {})}
          onApprove={props.onApprove}
          onReject={props.onReject}
          onOpenFile={props.onOpenFile}
          workspaceRoot={props.workspaceRoot}
          models={props.models}
          showTeamRunMarkers={false}
          refreshNonce={refreshNonce}
        />
      ) : (
        <EmptyState className="team-empty" icon={<TeamIcon />} title="尚未运行" />
      )}

      {open && <TeamPostComposer team={team} actions={actions} member={member} />}

      <ConfirmDialog
        open={confirmRemove !== undefined}
        title={`移除 ${member.name}`}
        isDestructive
        confirmLabel={confirmRemove === "discard" ? "放弃变更并移除" : "移除"}
        busy={actions.busy}
        description={confirmRemove === "discard" ? "成员 Worktree 中未整合的变更会被删除。" : "成员的运行会停止，未完成的任务回到待领取。"}
        onCancel={() => setConfirmRemove(undefined)}
        onConfirm={() => void remove(confirmRemove === "discard")}
      />
    </article>
  );
}

function TeamMemberChangesView({ team, member, refreshNonce }: {
  team: TeamView;
  member: TeamMemberView;
  refreshNonce: number;
}) {
  const [changes, setChanges] = useState<TeamMemberChanges>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const scopeKey = `${team.id}:${member.name}:${member.runId ?? ""}`;
  const lastScopeRef = useRef(scopeKey);

  useEffect(() => {
    let disposed = false;
    if (lastScopeRef.current !== scopeKey) {
      lastScopeRef.current = scopeKey;
      setChanges(undefined);
    }
    setError("");
    setLoading(true);
    window.eidosRuntime.team("team/memberChanges", { teamId: team.id, name: member.name })
      .then((value) => { if (!disposed) setChanges(value); })
      .catch((cause: unknown) => { if (!disposed) setError(userFacingError(cause)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [scopeKey, team.id, member.name, refreshNonce]);

  const more = async () => {
    if (!changes) return;
    setLoading(true);
    try {
      const page = await window.eidosRuntime.team("team/memberChanges", {
        teamId: team.id, name: member.name, offsetBytes: changes.diffNextOffset ?? 0,
      });
      setChanges({ ...page, diff: (changes.diff ?? "") + (page.diff ?? "") });
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setLoading(false);
    }
  };

  const files = changes?.files ?? [];
  if (!error && (!changes || files.length === 0)) return null;
  return (
    <details className="team-member-changes">
      <summary className="team-member-changes__summary">
        <GitBranchIcon width={13} height={13} />
        <span>Worktree 变更 · {files.length} 个文件</span>
      </summary>
      <section className="agent-changes" aria-label="变更">
        {error && <AlertBanner tone="error">{error}</AlertBanner>}
        {files.length > 0 && (
          <ul className="agent-changes__files">
            {files.map((file) => (
              <li key={file.path} className={`agent-changes__file agent-changes__file--${file.kind}`}>
                <span className="agent-changes__path">{file.path}</span>
                <span className="agent-changes__stat">{file.binary ? "binary" : `+${file.additions ?? 0} −${file.deletions ?? 0}`}</span>
              </li>
            ))}
          </ul>
        )}
        {changes?.diff && <pre className="agent-changes__diff">{changes.diff}</pre>}
        {changes?.diffHasMore && (
          <button type="button" className="agent-changes__more" disabled={loading} onClick={() => void more()}>
            {loading ? "加载中…" : "加载更多"}
          </button>
        )}
      </section>
    </details>
  );
}

// Tasks ------------------------------------------------------------------------------

function TaskStateIcon({ task }: { task: TeamTaskView }) {
  if (task.status === "done") return <span className="team-task__icon team-task__icon--done" aria-hidden="true"><GoalCheckIcon width={13} height={13} /></span>;
  if (task.status === "in_progress") return <span className="agent-status-dot agent-status-dot--running team-task__dot" aria-hidden="true" />;
  if (task.status === "in_review") return <span className="agent-status-dot agent-status-dot--waiting_approval team-task__dot" aria-hidden="true" />;
  if (task.kind === "gate") return <span className="team-task__icon team-task__icon--gate" aria-hidden="true"><AgentRoleIcon role="verifier" size={13} /></span>;
  if (task.blocked) return <span className="team-task__icon team-task__icon--blocked" aria-hidden="true"><LockIcon size={11.5} /></span>;
  if (task.status === "canceled") return <span className="team-task__icon team-task__icon--canceled" aria-hidden="true"><CloseIcon width={11} height={11} /></span>;
  return <span className="team-task__circle" aria-hidden="true" />;
}

function TeamTasks({
  team,
  actions,
  compact,
  highlightedTaskKey,
  onOpenMember,
}: {
  team: TeamView;
  actions: TeamActions;
  compact: boolean;
  highlightedTaskKey?: string | undefined;
  onOpenMember: (name: string) => void;
}) {
  const allTasks = tasks(team);
  const hasMilestones = allTasks.some((task) => Boolean(task.milestone));
  const [groupPreference, setGroupPreference] = useState<TaskGroupBy>();
  const [rejecting, setRejecting] = useState<string>();
  const [note, setNote] = useState("");
  const [expandedNotes, setExpandedNotes] = useState<ReadonlySet<string>>(() => new Set());
  const taskRefs = useRef(new Map<string, HTMLLIElement>());

  useEffect(() => {
    if (!highlightedTaskKey) return;
    const node = taskRefs.current.get(highlightedTaskKey);
    node?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, [highlightedTaskKey]);

  const effectiveGroupBy: TaskGroupBy = hasMilestones ? (groupPreference ?? "milestone") : "status";
  const closed = team.status === "dissolving" || team.status === "dissolved";

  const groups = useMemo(() => {
    if (effectiveGroupBy === "milestone") {
      const order: string[] = [];
      const bucket = new Map<string, TeamTaskView[]>();
      const ungrouped: TeamTaskView[] = [];
      for (const task of allTasks) {
        if (!task.milestone) {
          ungrouped.push(task);
          continue;
        }
        if (!bucket.has(task.milestone)) {
          bucket.set(task.milestone, []);
          order.push(task.milestone);
        }
        bucket.get(task.milestone)!.push(task);
      }
      order.sort((a, b) => a.localeCompare(b, "zh-Hans-CN", { numeric: true }));
      const result = order.map((label) => ({
        id: `ms:${label}`,
        label,
        tasks: bucket.get(label)!,
      }));
      if (ungrouped.length > 0) result.push({ id: "ms:__other__", label: "其他", tasks: ungrouped });
      return result;
    }
    return TASK_ORDER
      .map((status) => ({ id: `st:${status}`, label: TASK_STATUS_LABELS[status], tasks: allTasks.filter((task) => task.status === status) }))
      .filter((group) => group.tasks.length > 0);
  }, [allTasks, effectiveGroupBy]);

  if (allTasks.length === 0) return <EmptyState className="team-empty" icon={<TeamIcon />} title="暂无任务" />;

  const doneCount = allTasks.filter((task) => task.status === "done").length;
  const pct = Math.round((doneCount / allTasks.length) * 100);

  const op = (task: TeamTaskView, operation: "accept" | "reject" | "cancel" | "reopen", text?: string) =>
    void actions.execute("team/taskUpdate", { teamId: team.id, request: { op: operation, key: task.key, ...(text ? { note: text } : {}) } });

  const toggleNote = (key: string) => setExpandedNotes((previous) => {
    const next = new Set(previous);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  return (
    <div className={`team-tasks${compact ? " team-tasks--compact" : ""}`}>
      <div className="team-tasks__toolbar">
        <div className="team-tasks__progress-group">
          <span className="team-tasks__heading">任务</span>
          <span className="team-tasks__ratio">{doneCount}/{allTasks.length}</span>
          <div className="team-tasks__progress-track" aria-hidden="true">
            <div className="team-tasks__progress-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
        {hasMilestones && (
          <Tooltip content={effectiveGroupBy === "milestone" ? "当前按里程碑分组（点击切为按状态）" : "当前按状态分组（点击切为按里程碑）"}>
            <button
              type="button"
              className="icon-button team-tasks__group-btn"
              aria-label="切换任务分组方式"
              onClick={() => setGroupPreference((current) => ((current ?? effectiveGroupBy) === "milestone" ? "status" : "milestone"))}
            >
              <TaskScopeIcon width={13} height={13} />
              <span>{effectiveGroupBy === "milestone" ? "里程碑" : "状态"}</span>
            </button>
          </Tooltip>
        )}
      </div>

      {groups.map((group) => (
        <section key={group.id} className="team-tasks__group" aria-label={group.label}>
          <h4>{group.label}</h4>
          <ul>
            {group.tasks.map((task) => {
              const isGate = task.kind === "gate";
              const isReview = task.status === "in_review";
              const delivery = deliveryLabel(task.deliveryStatus);
              const noteExpanded = expandedNotes.has(task.key);
              const secondaryMenuItems: DropdownMenuItem[] = [];
              if (!closed && task.status === "done" && !isGate && task.milestone) {
                secondaryMenuItems.push({
                  key: "reopen",
                  label: "重新打开…",
                  disabled: actions.busy,
                  onClick: () => { setRejecting(task.key); setNote(""); },
                });
              }
              if (!closed && (task.status === "open" || task.status === "in_progress")) {
                secondaryMenuItems.push({
                  key: "cancel",
                  label: "取消任务",
                  danger: true,
                  disabled: actions.busy,
                  onClick: () => op(task, "cancel"),
                });
              }

              const taskTooltipRows: TooltipRow[] = [
                { label: "负责人", value: task.owner ? `@${task.owner}` : "未分配" },
                ...(task.reviewer ? [{ label: "审查者", value: `@${task.reviewer}` }] : []),
                ...(task.blocked && (task.dependsOn?.length ?? 0) > 0
                  ? [{ label: "阻塞等待", value: task.dependsOn!.join(", "), tone: "waiting" as const }]
                  : []),
                ...((task.evidence?.length ?? 0) > 0 ? [{ label: "证据", value: `${task.evidence!.length} 项` }] : []),
                ...(delivery ? [{ label: "整合状态", value: delivery }] : []),
                ...(task.note ? [{ label: "说明", value: task.note }] : []),
              ];
              const taskTooltip = (
                <TeamTooltipCard
                  title={`#${task.key}${isGate ? " · 关口" : ""}`}
                  badge={TASK_STATUS_LABELS[task.status]}
                  rows={taskTooltipRows}
                />
              );

              return (
                <li
                  key={task.id}
                  ref={(node) => {
                    if (node) taskRefs.current.set(task.key, node);
                    else taskRefs.current.delete(task.key);
                  }}
                  data-task-key={task.key}
                  data-task-kind={task.kind ?? "work"}
                  className={[
                    "team-task",
                    `team-task--${task.status}`,
                    isGate ? "team-task--gate" : "",
                    task.blocked ? "team-task--blocked" : "",
                    highlightedTaskKey === task.key ? "team-task--flash" : "",
                  ].filter(Boolean).join(" ")}
                >
                  <div className="team-task__row">
                    <div className="team-task__main">
                      <Tooltip content={taskTooltip} placement={compact ? "left" : "top"} className="team-detail-tooltip">
                        <div className="team-task__head">
                          <TaskStateIcon task={task} />
                          <span className="team-task__title">{task.title}</span>
                        </div>
                      </Tooltip>
                      <div className="team-task__meta">
                        {task.owner ? (
                          <button type="button" className="team-task__owner" onClick={() => onOpenMember(task.owner!)}>
                            @{task.owner}
                          </button>
                        ) : (
                          <span>未分配</span>
                        )}
                        {effectiveGroupBy === "status" && task.milestone && <span>· {task.milestone}</span>}
                        {delivery && <span className="team-task__delivery">· {delivery}</span>}
                        {task.note && (
                          <Tooltip content={noteExpanded ? "收起说明" : "查看说明"}>
                            <button
                              type="button"
                              className={`team-task__note-toggle${noteExpanded ? " is-open" : ""}`}
                              aria-expanded={noteExpanded}
                              aria-label="查看任务说明"
                              onClick={() => toggleNote(task.key)}
                            >
                              <FileDiffIcon width={11.5} height={11.5} />
                            </button>
                          </Tooltip>
                        )}
                      </div>
                    </div>

                    {secondaryMenuItems.length > 0 && rejecting !== task.key && (
                      <div className="team-task__menu">
                        <DropdownMenu
                          align="right"
                          label={`${task.title} 操作`}
                          triggerAriaLabel={`${task.title} 操作`}
                          triggerClassName="team-task__menu-trigger"
                          trigger={<MoreHorizontalIcon />}
                          items={secondaryMenuItems}
                        />
                      </div>
                    )}
                  </div>

                  {task.note && noteExpanded && <p className="team-task__note">{task.note}</p>}

                  {!closed && isReview && rejecting !== task.key && (
                    <div className="team-task__actions">
                      <Button size="small" variant="primary" disabled={actions.busy} onClick={() => op(task, "accept")}>接受</Button>
                      <Button size="small" variant="secondary" disabled={actions.busy} onClick={() => { setRejecting(task.key); setNote(""); }}>退回</Button>
                    </div>
                  )}

                  {!closed && rejecting === task.key && (
                    <form
                      className="team-task__reject"
                      onSubmit={(event) => {
                        event.preventDefault();
                        if (!note.trim()) return;
                        op(task, task.status === "done" ? "reopen" : "reject", note.trim());
                        setRejecting(undefined);
                      }}
                    >
                      <textarea
                        rows={2}
                        aria-label="原因"
                        placeholder={task.status === "done" ? "重新打开原因…" : "退回原因…"}
                        value={note}
                        maxLength={2000}
                        autoFocus
                        onChange={(event) => setNote(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") {
                            event.preventDefault();
                            setRejecting(undefined);
                          } else if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                            event.preventDefault();
                            if (!note.trim() || actions.busy) return;
                            op(task, task.status === "done" ? "reopen" : "reject", note.trim());
                            setRejecting(undefined);
                          }
                        }}
                      />
                      <div className="team-task__reject-actions">
                        <Button type="submit" size="small" variant="primary" disabled={!note.trim() || actions.busy}>
                          {task.status === "done" ? "确认" : "退回"}
                        </Button>
                        <Button size="small" variant="ghost" onClick={() => setRejecting(undefined)}>取消</Button>
                      </div>
                    </form>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
