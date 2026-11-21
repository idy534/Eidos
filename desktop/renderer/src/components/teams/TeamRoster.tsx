import { useLayoutEffect, useRef, useState } from "react";
import type { RosterDecision, TeamProposalView } from "../../../../shared/teams.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { TeamIcon } from "../InputPicker.js";
import { CheckmarkIcon, ChevronDownIcon } from "../MenuIcons.js";
import { Select } from "../Select.js";
import { Tooltip } from "../Tooltip.js";
import { Toggle } from "../settings/Toggle.js";
import { MEMBER_ROLE_LABELS, memberRolePillClass } from "./team-labels.js";

type ApprovalMode = RosterDecision["approvalMode"];

const APPROVAL_LABELS: Record<NonNullable<ApprovalMode>, string> = {
  manual: "手动审批",
  auto_review: "自动审查",
  full_access: "完全访问",
};

/**
 * The lead's team proposal waits for the user here, in place of the Composer,
 * like a clarification question. Edits may only drop members or switch a
 * member's workspace; anything else goes back to the lead as a decline.
 */
export function TeamRoster({ proposal, approvalMode, modelLabel, worktreeAvailable, busy, error, onDecide }: {
  proposal: TeamProposalView;
  approvalMode: NonNullable<ApprovalMode>;
  modelLabel: string | undefined;
  worktreeAvailable: boolean;
  busy: boolean;
  error: string | undefined;
  onDecide: (decision: RosterDecision) => void;
}) {
  const plan = proposal.proposal;
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set());
  const [expandedMembers, setExpandedMembers] = useState<ReadonlySet<string>>(new Set());
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [summaryOverflow, setSummaryOverflow] = useState(false);
  const summaryRef = useRef<HTMLParagraphElement>(null);
  const [workspaces, setWorkspaces] = useState<Record<string, "shared" | "worktree">>(
    () => Object.fromEntries(plan.members.map((member) => [member.name, worktreeAvailable ? member.workspace ?? "shared" : "shared"])));
  const [autoWake, setAutoWake] = useState(true);
  const kept = plan.members.filter((member) => !removed.has(member.name));
  const openTasks = (plan.tasks ?? []).filter((task) => !task.owner);

  useLayoutEffect(() => {
    const node = summaryRef.current;
    if (!node || !plan.summary) {
      setSummaryOverflow(false);
      return;
    }
    const measure = () => {
      if (node.clientWidth === 0) {
        setSummaryOverflow(plan.summary!.length > 48 || plan.summary!.includes("\n"));
        return;
      }
      setSummaryOverflow(node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [plan.summary, summaryExpanded]);

  const toggle = (name: string) => setRemoved((previous) => {
    const next = new Set(previous);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });

  const toggleExpanded = (name: string) => setExpandedMembers((previous) => {
    const next = new Set(previous);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });

  const decide = (confirmed: boolean) => onDecide({
    confirmed,
    removedMembers: [...removed],
    workspaces: Object.fromEntries(kept.map((member) => [member.name, workspaces[member.name] ?? "shared"])),
    approvalMode,
    ...(approvalMode === "full_access" ? { fullAccessConfirmation: "full-access-v1" as const } : {}),
    autoWakeLead: autoWake,
  });

  return (
    <section className="team-roster" aria-label="团队编制">
      <header className="team-roster__header">
        <span className="team-roster__icon" aria-hidden="true"><TeamIcon /></span>
        <Tooltip content={plan.objective} placement="top">
          <h3 className="team-roster__objective">{plan.objective}</h3>
        </Tooltip>
        <span className="team-roster__count">{kept.length}/{plan.members.length}</span>
      </header>

      {plan.summary && (
        <div className={`team-roster__summary-bar${summaryExpanded ? " is-expanded" : ""}`}>
          <p
            ref={summaryRef}
            className={`team-roster__summary${summaryExpanded ? "" : " team-roster__summary--clamped"}`}
          >
            {plan.summary}
          </p>
          {(summaryOverflow || summaryExpanded) && (
            <Tooltip content={summaryExpanded ? "收起方案说明" : "展开完整方案说明"} placement="top">
              <button
                type="button"
                className="team-roster__summary-toggle"
                aria-expanded={summaryExpanded}
                aria-label={summaryExpanded ? "收起方案说明" : "展开方案说明"}
                onClick={() => setSummaryExpanded((previous) => !previous)}
              >
                <ChevronDownIcon className={`user-message-expand-icon${summaryExpanded ? " is-expanded" : ""}`} />
              </button>
            </Tooltip>
          )}
        </div>
      )}

      <ul className="team-roster__members">
        {plan.members.map((member) => {
          const off = removed.has(member.name);
          const expanded = expandedMembers.has(member.name);
          const role = member.role ?? "worker";
          const roleLabel = MEMBER_ROLE_LABELS[role] ?? role;
          const tasks = (plan.tasks ?? []).filter((task) => task.owner === member.name);
          const primaryTask = tasks[0];
          const extraTasks = tasks.length > 1 ? tasks.length - 1 : 0;
          const inlineText = primaryTask ? primaryTask.title : member.brief;
          const hasDetails = Boolean(member.brief || tasks.length > 0);

          return (
            <li
              key={member.name}
              className={`team-roster__member${off ? " team-roster__member--removed" : ""}${expanded ? " is-expanded" : ""}`}
            >
              <div className="team-roster__row">
                <label className="team-roster__keep">
                  <input
                    type="checkbox"
                    className="team-roster__checkbox-input"
                    checked={!off}
                    disabled={busy}
                    aria-label={`保留 ${member.name}`}
                    onChange={() => toggle(member.name)}
                  />
                  <span className={`clarification-checkbox${!off ? " checked" : ""}`} aria-hidden="true">
                    {!off && <CheckmarkIcon width={10} height={10} strokeWidth={2.2} />}
                  </span>
                </label>

                <button
                  type="button"
                  className="team-roster__member-toggle"
                  disabled={!hasDetails}
                  aria-expanded={hasDetails ? expanded : undefined}
                  aria-label={`${expanded ? "收起" : "展开"} ${member.name} 详情`}
                  onClick={() => hasDetails && toggleExpanded(member.name)}
                >
                  <span className="team-roster__identity">
                    <span className="team-roster__name">{member.name}</span>
                    <span className={memberRolePillClass(role)}>{roleLabel}</span>
                  </span>
                  {inlineText && (
                    <span className="team-roster__task-slot">
                      <span className="team-roster__task-inline">{inlineText}</span>
                      {extraTasks > 0 && <span className="team-roster__task-more">+{extraTasks}</span>}
                    </span>
                  )}
                </button>

                <div className="team-roster__row-right">
                  {worktreeAvailable && (
                    <Select
                      className="team-roster__workspace"
                      aria-label={`${member.name} 的工作区`}
                      disabled={busy || off}
                      value={workspaces[member.name] ?? "shared"}
                      onChange={(event) => setWorkspaces((previous) => ({
                        ...previous,
                        [member.name]: event.target.value === "worktree" ? "worktree" : "shared",
                      }))}
                    >
                      <option value="shared">共享工作区</option>
                      <option value="worktree">独立 Worktree</option>
                    </Select>
                  )}
                  {hasDetails && (
                    <button
                      type="button"
                      className="team-roster__expand-btn"
                      aria-expanded={expanded}
                      aria-label={`${expanded ? "收起" : "查看"} ${member.name} 详细职责`}
                      onClick={() => toggleExpanded(member.name)}
                    >
                      <ChevronDownIcon className={`user-message-expand-icon${expanded ? " is-expanded" : ""}`} />
                    </button>
                  )}
                </div>
              </div>

              {expanded && hasDetails && (
                <div className="team-roster__drawer">
                  {member.brief && <p className="team-roster__brief">{member.brief}</p>}
                  {tasks.length > 0 && (
                    <ul className="team-roster__tasks" aria-label={`${member.name} 的初始任务`}>
                      {tasks.map((task) => {
                        const isGate = task.kind === "gate";
                        return (
                          <li key={task.key} className="team-roster__subtask">
                            <span
                              className={`team-roster__subtask-dot${isGate ? " team-roster__subtask-dot--gate" : ""}`}
                              aria-hidden="true"
                            />
                            {isGate && <span className="team-roster__gate-tag">关口</span>}
                            <span className="team-roster__subtask-title">{task.title}</span>
                            {task.milestone && <span className="team-roster__subtask-milestone">{task.milestone}</span>}
                            <code className="team-roster__subtask-key">#{task.key}</code>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {openTasks.length > 0 && (
        <div className="team-roster__lead-strip" aria-label="待领取任务">
          <span className="team-roster__lead-badge">未指派 / Lead</span>
          <ul className="team-roster__lead-list">
            {openTasks.map((task) => (
              <li key={task.key} className="team-roster__lead-item">
                <Tooltip content={`#${task.key}${task.milestone ? ` · ${task.milestone}` : ""} · ${task.title}`} placement="top">
                  <span>{task.title}</span>
                </Tooltip>
              </li>
            ))}
          </ul>
        </div>
      )}

      <footer className="team-roster__footer">
        <span className="team-roster__settings">
          {modelLabel && <span className="team-roster__setting-chip">{modelLabel}</span>}
          <span className={`team-roster__setting-chip${approvalMode === "full_access" ? " team-roster__setting-chip--warn" : ""}`}>
            {APPROVAL_LABELS[approvalMode]}
          </span>
          <Tooltip content="成员汇报结果或完成任务时自动唤醒 Lead 继续推进" placement="top">
            <span className="team-roster__toggle-wrap">
              <Toggle checked={autoWake} disabled={busy} label="自动续跑" onChange={setAutoWake} />
              <span className="team-roster__toggle-text">自动续跑</span>
            </span>
          </Tooltip>
        </span>
        <span className="team-roster__actions">
          <Button variant="ghost" size="small" disabled={busy} onClick={() => decide(false)}>拒绝</Button>
          <Button variant="primary" size="small" disabled={busy || kept.length === 0} onClick={() => decide(true)}>
            {busy ? "提交中…" : "组建团队"}
          </Button>
        </span>
      </footer>
      {error && <AlertBanner tone="error">{error}</AlertBanner>}
    </section>
  );
}
