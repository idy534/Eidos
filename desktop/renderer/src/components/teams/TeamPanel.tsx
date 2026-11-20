import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  TeamMemberChanges, TeamMemberView, TeamMessageView, TeamTaskView, TeamUsage, TeamView,
} from "../../../../shared/teams.js";
import type { ApprovalRequest, ModelOption } from "../../contracts.js";
import { RUNTIME_ERROR_MESSAGES, userFacingError } from "../../session-state.js";
import { AgentTranscript } from "../AgentPanel.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { statusText } from "../Composer.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { EmptyState } from "../EmptyState.js";
import { TeamIcon } from "../InputPicker.js";
import { ACTIVE_RUN, MEMBER_ROLE_LABELS, TASK_STATUS_LABELS, TEAM_STATUS_LABELS, formatTokens } from "./team-labels.js";
import type { useTeam } from "./useTeam.js";

type TeamActions = Pick<ReturnType<typeof useTeam>, "execute" | "busy" | "error">;
type View = "board" | "members" | "tasks";

const VIEWS: { id: View; label: string }[] = [
  { id: "board", label: "看板" }, { id: "members", label: "成员" }, { id: "tasks", label: "任务" },
];
const TASK_ORDER: TeamTaskView["status"][] = ["in_review", "in_progress", "open", "done", "canceled"];
const MESSAGE_PAGE = 100;
const MESSAGE_PAGES = 10;

function members(team: TeamView): TeamMemberView[] { return team.members ?? []; }
function tasks(team: TeamView): TeamTaskView[] { return team.tasks ?? []; }

function usageText(usage: readonly TeamUsage[]): string | undefined {
  const total = usage.reduce((sum, value) => sum + (value.inputTokens ?? 0) + (value.outputTokens ?? 0), 0);
  return formatTokens(total, usage.some((value) => value.incomplete));
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

/** The lead Session's team: board, members and tasks, plus one member's detail. */
export function TeamPanel(input: TeamPanelProps) {
  // Generated DTOs mark defaulted lists optional; the panel reads them as present.
  const props: TeamPanelProps = { ...input, team: { ...input.team, members: members(input.team), tasks: tasks(input.team) } };
  const { team, usage, actions, memberName } = props;
  const [view, setView] = useState<View>("board");
  const [confirmDissolve, setConfirmDissolve] = useState(false);
  const member = memberName ? members(team).find((entry) => entry.name === memberName) : undefined;
  const approvalsBySession = useMemo(() => {
    const counts = new Map<string, number>();
    for (const request of props.approvals) counts.set(request.sessionId, (counts.get(request.sessionId) ?? 0) + 1);
    return counts;
  }, [props.approvals]);
  const closed = team.status === "dissolving" || team.status === "dissolved";
  const busyMembers = members(team).filter((entry) => entry.runStatus && ACTIVE_RUN.has(entry.runStatus)).length;
  const tokens = usageText(usage);

  if (member) {
    return <TeamMemberDetail {...props} member={member} pendingApprovals={approvalsBySession.get(member.sessionId) ?? 0}
      memberUsage={usage.find((value) => value.member === member.name)} />;
  }

  return (
    <section className="team-panel" aria-label="团队">
      <header className="team-panel__header">
        <div className="team-panel__title-row">
          <span className="team-panel__icon" aria-hidden="true"><TeamIcon /></span>
          <h2 className="team-panel__objective" title={team.objective}>{team.objective}</h2>
          <span className={`team-panel__status team-panel__status--${team.status}`}>{TEAM_STATUS_LABELS[team.status]}</span>
        </div>
        <div className="team-panel__meta">
          <span>{members(team).filter((entry) => entry.status === "active").length} 名成员</span>
          {busyMembers > 0 && <span>{busyMembers} 名运行中</span>}
          {tokens && <span>{tokens}</span>}
        </div>
        {!closed && (
          <div className="team-panel__actions">
            <Button size="small" variant="ghost" disabled={actions.busy}
              onClick={() => void actions.execute("team/setStatus", { teamId: team.id, status: team.status === "paused" ? "active" : "paused" })}>
              {team.status === "paused" ? "继续" : "暂停"}
            </Button>
            <Button size="small" variant="ghost" disabled={actions.busy || busyMembers === 0}
              onClick={() => void actions.execute("team/stopMembers", { teamId: team.id })}>停止成员</Button>
            <label className="team-panel__toggle">
              <input type="checkbox" checked={team.autoWakeLead} disabled={actions.busy}
                onChange={(event) => void actions.execute("team/updateSettings", { teamId: team.id, autoWakeLead: event.target.checked })} />
              自动续跑
            </label>
            <Button size="small" variant="ghost" className="team-panel__danger" disabled={actions.busy}
              onClick={() => setConfirmDissolve(true)}>解散</Button>
          </div>
        )}
        <div className="team-panel__views" role="tablist" aria-label="团队视图">
          {VIEWS.map((entry) => (
            <button key={entry.id} type="button" role="tab" aria-selected={view === entry.id}
              className={`team-panel__view${view === entry.id ? " team-panel__view--active" : ""}`}
              onClick={() => setView(entry.id)}>{entry.label}</button>
          ))}
        </div>
        <select className="team-panel__view-select" aria-label="团队视图" value={view}
          onChange={(event) => setView(event.target.value as View)}>
          {VIEWS.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
        </select>
      </header>
      {actions.error && <AlertBanner tone="error">{actions.error}</AlertBanner>}
      {view === "board" && <TeamBoard team={team} actions={actions} />}
      {view === "members" && <TeamMembers team={team} usage={usage} approvals={approvalsBySession} onOpen={props.onOpenMember} />}
      {view === "tasks" && <TeamTasks team={team} actions={actions} />}
      <ConfirmDialog open={confirmDissolve} title="解散团队" isDestructive confirmLabel="解散" busy={actions.busy}
        description="停止所有成员，删除成员的 Worktree。未整合的变更会丢失。"
        onCancel={() => setConfirmDissolve(false)}
        onConfirm={() => { setConfirmDissolve(false); void actions.execute("team/setStatus", { teamId: team.id, status: "dissolving" }); }} />
    </section>
  );
}

// Board ------------------------------------------------------------------------------

function TeamBoard({ team, actions }: { team: TeamView; actions: TeamActions }) {
  const [messages, setMessages] = useState<TeamMessageView[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState("");
  const [memberFilter, setMemberFilter] = useState("");
  const [channelFilter, setChannelFilter] = useState<"" | "board" | "direct">("");
  const cursor = useRef(0);
  const loading = useRef(false);
  const pending = useRef(false);
  const generation = useRef(0);

  const load = useCallback(async (reset: boolean) => {
    if (loading.current) { pending.current = true; return; }
    loading.current = true;
    const token = reset ? ++generation.current : generation.current;
    if (reset) cursor.current = 0;
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
      if (pending.current) { pending.current = false; void load(false); }
    }
  }, [team.id, memberFilter, channelFilter]);

  useEffect(() => { void load(true); }, [load]);
  // Every board change bumps the team version; pull only what is new.
  useEffect(() => { void load(false); }, [team.version, load]);

  const names = members(team).map((member) => member.name);
  return (
    <div className="team-board">
      <div className="team-board__filters">
        <select aria-label="按成员筛选" value={memberFilter} onChange={(event) => setMemberFilter(event.target.value)}>
          <option value="">全部成员</option>
          {names.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <select aria-label="按频道筛选" value={channelFilter}
          onChange={(event) => setChannelFilter(event.target.value === "board" || event.target.value === "direct" ? event.target.value : "")}>
          <option value="">全部频道</option>
          <option value="board">看板</option>
          <option value="direct">私聊</option>
        </select>
      </div>
      {error && <AlertBanner tone="error">{error}</AlertBanner>}
      {messages.length === 0 && !error
        ? <EmptyState className="team-empty" icon={<TeamIcon />} title="暂无消息" />
        : (
          <ol className="team-board__messages" aria-label="团队消息">
            {truncated && <li className="team-board__more">更多消息未加载</li>}
            {messages.map((message) => <TeamMessage key={message.id} message={message} />)}
          </ol>
        )}
      <TeamPostComposer team={team} actions={actions} />
    </div>
  );
}

function TeamMessage({ message }: { message: TeamMessageView }) {
  const recipients = message.toAll ? ["all"] : message.recipients ?? [];
  return (
    <li className={`team-message team-message--${message.authorKind}${message.channel === "direct" ? " team-message--direct" : ""}`}>
      <div className="team-message__head">
        <span className="team-message__author">{message.authorKind === "user" ? "你" : message.authorKind === "system" ? "系统" : message.author}</span>
        {recipients.length > 0 && <span className="team-message__to">→ {recipients.join(", ")}</span>}
        {message.channel === "direct" && <span className="team-message__tag">私聊</span>}
        {message.task && <code className="team-message__task">{message.task}</code>}
        <time className="team-message__time" dateTime={new Date(message.createdAt).toISOString()}>
          {new Date(message.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </time>
      </div>
      <p className="team-message__body">{message.content}</p>
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

  const send = async () => {
    const value = text.trim();
    if (!value || actions.busy || closed) return;
    if (await actions.execute("team/post", { teamId: team.id, message: { to: [target], channel: isDirect ? "direct" : "board", text: value } })) {
      setText("");
    }
  };

  return (
    <form className="team-post" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      {!member && (
        <div className="team-post__target">
          <select aria-label="发给" value={recipient} disabled={closed}
            onChange={(event) => { setRecipient(event.target.value); if (event.target.value === "all") setDirect(false); }}>
            <option value="lead">lead</option>
            {members(team).filter((entry) => entry.status === "active").map((entry) => <option key={entry.name} value={entry.name}>{entry.name}</option>)}
            <option value="all">全体</option>
          </select>
          <label className="team-post__direct">
            <input type="checkbox" checked={isDirect} disabled={closed || recipient === "all"} onChange={(event) => setDirect(event.target.checked)} />
            私聊
          </label>
        </div>
      )}
      <div className="team-post__row">
        <textarea rows={2} value={text} disabled={closed}
          aria-label={`发给 ${target === "all" ? "全体" : target}`} placeholder={`发给 ${target === "all" ? "全体" : target}`}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }} />
        <Button type="submit" size="small" variant="primary" disabled={!text.trim() || actions.busy || closed}>发送</Button>
      </div>
    </form>
  );
}

// Members ------------------------------------------------------------------------------

function memberState(member: TeamMemberView): string {
  if (member.status === "removed") return "已移除";
  return member.runStatus && ACTIVE_RUN.has(member.runStatus) ? statusText(member.runStatus) : "空闲";
}

function TeamMembers({ team, usage, approvals, onOpen }: {
  team: TeamView; usage: readonly TeamUsage[]; approvals: ReadonlyMap<string, number>; onOpen: (name: string) => void;
}) {
  return (
    <ul className="team-members" aria-label="团队成员">
      {members(team).map((member) => {
        const busy = member.runStatus ? ACTIVE_RUN.has(member.runStatus) : false;
        const pending = approvals.get(member.sessionId) ?? 0;
        const value = usage.find((entry) => entry.member === member.name);
        const tokens = value ? formatTokens((value.inputTokens ?? 0) + (value.outputTokens ?? 0), value.incomplete ?? false) : undefined;
        return (
          <li key={member.id}>
            <button type="button" className={`team-member${busy ? " team-member--busy" : ""}${member.status === "removed" ? " team-member--removed" : ""}`}
              onClick={() => onOpen(member.name)}>
              <span className="team-member__name">{member.name}</span>
              <span className="team-member__role">{MEMBER_ROLE_LABELS[member.role] ?? member.role}</span>
              <span className="team-member__state">{memberState(member)}</span>
              {pending > 0 && <span className="team-member__approvals" aria-label={`${pending} 个待审批`}>{pending}</span>}
              <span className="team-member__detail">
                <span>{member.workspaceMode === "worktree" ? "Worktree" : "共享"}</span>
                <span>{member.modelId}</span>
                {tokens && <span>{tokens}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function TeamMemberDetail(props: TeamPanelProps & { member: TeamMemberView; pendingApprovals: number; memberUsage: TeamUsage | undefined }) {
  const { team, member, actions } = props;
  const [confirmRemove, setConfirmRemove] = useState<"keep" | "discard">();
  const busy = member.runStatus ? ACTIVE_RUN.has(member.runStatus) : false;
  const own = props.memberUsage;
  const tokens = own ? formatTokens((own.inputTokens ?? 0) + (own.outputTokens ?? 0), own.incomplete ?? false) : undefined;
  const open = team.status !== "dissolving" && team.status !== "dissolved" && member.status === "active";
  const owned = tasks(team).filter((task) => task.owner === member.name && task.status !== "done" && task.status !== "canceled");

  const remove = async (discardChanges: boolean) => {
    setConfirmRemove(undefined);
    if (await actions.execute("team/removeMember", { teamId: team.id, name: member.name, discardChanges })) props.onBack();
  };

  return (
    <article className="team-member-detail" aria-label={`成员 ${member.name}`}>
      <header className="team-member-detail__header">
        <Button size="small" variant="ghost" onClick={props.onBack} aria-label="返回团队">←</Button>
        <h2>{member.name}</h2>
        <span className="team-member__role">{MEMBER_ROLE_LABELS[member.role] ?? member.role}</span>
        <span className="team-member__state">{memberState(member)}</span>
        {tokens && <span className="team-member-detail__usage">{tokens}</span>}
        <span className="team-member-detail__actions">
          {busy && member.runId && <Button size="small" variant="ghost" onClick={() => props.onCancelRun(member.runId!, member.sessionId)}>停止</Button>}
          {open && <Button size="small" variant="ghost" className="team-panel__danger" disabled={actions.busy}
            onClick={() => setConfirmRemove("keep")}>移除</Button>}
        </span>
      </header>
      <p className="team-member-detail__brief">{member.brief}</p>
      {owned.length > 0 && (
        <ul className="team-member-detail__tasks">
          {owned.map((task) => <li key={task.key}><code>{task.key}</code>{task.title}<span>{TASK_STATUS_LABELS[task.status]}</span></li>)}
        </ul>
      )}
      {actions.error && (
        <AlertBanner tone="error" action={actions.error === RUNTIME_ERROR_MESSAGES.TEAM_MEMBER_HAS_CHANGES && open
          ? <Button size="small" variant="danger" onClick={() => setConfirmRemove("discard")}>放弃变更并移除</Button> : undefined}>
          {actions.error}
        </AlertBanner>
      )}
      {member.workspaceMode === "worktree" && !busy && <TeamMemberChangesView team={team} member={member} />}
      {member.runId
        ? <AgentTranscript
            agent={{ id: member.id, sessionId: member.sessionId, runId: member.runId, status: member.runStatus ?? "succeeded",
              createdAt: member.createdAt, task: member.brief, taskName: member.name }}
            approvals={props.approvals}
            {...(props.respondingApprovalIds ? { respondingApprovalIds: props.respondingApprovalIds } : {})}
            {...(props.respondingKindByApprovalId ? { respondingKindByApprovalId: props.respondingKindByApprovalId } : {})}
            {...(props.expiredApprovalIds ? { expiredApprovalIds: props.expiredApprovalIds } : {})}
            {...(props.errorsByApprovalId ? { errorsByApprovalId: props.errorsByApprovalId } : {})}
            onApprove={props.onApprove}
            onReject={props.onReject}
            onOpenFile={props.onOpenFile}
            workspaceRoot={props.workspaceRoot}
            models={props.models} />
        : <EmptyState className="team-empty" icon={<TeamIcon />} title="尚未运行" />}
      {open && <TeamPostComposer team={team} actions={actions} member={member} />}
      <ConfirmDialog open={confirmRemove !== undefined} title={`移除 ${member.name}`} isDestructive
        confirmLabel={confirmRemove === "discard" ? "放弃变更并移除" : "移除"} busy={actions.busy}
        description={confirmRemove === "discard" ? "成员 Worktree 中未整合的变更会被删除。" : "成员的运行会停止，未完成的任务回到待领取。"}
        onCancel={() => setConfirmRemove(undefined)}
        onConfirm={() => void remove(confirmRemove === "discard")} />
    </article>
  );
}

function TeamMemberChangesView({ team, member }: { team: TeamView; member: TeamMemberView }) {
  const [changes, setChanges] = useState<TeamMemberChanges>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let disposed = false;
    setChanges(undefined);
    setError("");
    setLoading(true);
    window.eidosRuntime.team("team/memberChanges", { teamId: team.id, name: member.name })
      .then((value) => { if (!disposed) setChanges(value); })
      .catch((cause: unknown) => { if (!disposed) setError(userFacingError(cause)); })
      .finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [team.id, member.name, member.runId]);

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
    <section className="agent-changes" aria-label="变更">
      <header className="agent-changes__header"><h3>变更 · {files.length}</h3></header>
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
  );
}

// Tasks ------------------------------------------------------------------------------

function TeamTasks({ team, actions }: { team: TeamView; actions: TeamActions }) {
  const [rejecting, setRejecting] = useState<string>();
  const [note, setNote] = useState("");
  const closed = team.status === "dissolving" || team.status === "dissolved";
  if (tasks(team).length === 0) return <EmptyState className="team-empty" icon={<TeamIcon />} title="暂无任务" />;
  const groups = TASK_ORDER.map((status) => ({ status, tasks: tasks(team).filter((task) => task.status === status) }))
    .filter((group) => group.tasks.length > 0);
  const op = (task: TeamTaskView, operation: "accept" | "reject" | "cancel" | "reopen", text?: string) =>
    void actions.execute("team/taskUpdate", { teamId: team.id, request: { op: operation, key: task.key, ...(text ? { note: text } : {}) } });

  return (
    <div className="team-tasks">
      {groups.map((group) => (
        <section key={group.status} className="team-tasks__group" aria-label={TASK_STATUS_LABELS[group.status]}>
          <h3>{TASK_STATUS_LABELS[group.status]} · {group.tasks.length}</h3>
          <ul>
            {group.tasks.map((task) => (
              <li key={task.id} className={`team-task team-task--${task.status}${task.blocked ? " team-task--blocked" : ""}`}>
                <div className="team-task__head">
                  <code className="team-task__key">{task.key}</code>
                  {task.kind === "gate" && <span className="team-task__gate">关口</span>}
                  <span className="team-task__title">{task.title}</span>
                  {task.milestone && <span className="team-task__milestone">{task.milestone}</span>}
                </div>
                <div className="team-task__meta">
                  <span>{task.owner ?? "未分配"}</span>
                  {task.reviewer && <span>审查：{task.reviewer}</span>}
                  {task.blocked && <span>等待 {(task.dependsOn ?? []).join(", ")}</span>}
                  {(task.evidence?.length ?? 0) > 0 && <span>{task.evidence!.length} 项证据</span>}
                  {task.deliveryStatus && <span>{task.deliveryStatus === "integrated" ? "已整合" : task.deliveryStatus === "partially_integrated" ? "部分整合" : "待整合"}</span>}
                </div>
                {task.note && <p className="team-task__note">{task.note}</p>}
                {!closed && (
                  <div className="team-task__actions">
                    {task.status === "done" && task.kind !== "gate" && task.milestone && rejecting !== task.key && (
                      <Button size="small" variant="ghost" disabled={actions.busy} onClick={() => { setRejecting(task.key); setNote(""); }}>重新打开</Button>
                    )}
                    {task.status === "in_review" && rejecting !== task.key && <>
                      <Button size="small" variant="primary" disabled={actions.busy} onClick={() => op(task, "accept")}>接受</Button>
                      <Button size="small" variant="ghost" disabled={actions.busy} onClick={() => { setRejecting(task.key); setNote(""); }}>退回</Button>
                    </>}
                    {rejecting === task.key && (
                      <form className="team-task__reject" onSubmit={(event) => {
                        event.preventDefault();
                        if (!note.trim()) return;
                        op(task, task.status === "done" ? "reopen" : "reject", note.trim());
                        setRejecting(undefined);
                      }}>
                        <input aria-label="原因" placeholder="原因" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} />
                        <Button type="submit" size="small" variant="primary" disabled={!note.trim() || actions.busy}>
                          {task.status === "done" ? "重新打开" : "退回"}
                        </Button>
                        <Button size="small" variant="ghost" onClick={() => setRejecting(undefined)}>取消</Button>
                      </form>
                    )}
                    {(task.status === "open" || task.status === "in_progress") && (
                      <Button size="small" variant="ghost" disabled={actions.busy} onClick={() => op(task, "cancel")}>取消任务</Button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
