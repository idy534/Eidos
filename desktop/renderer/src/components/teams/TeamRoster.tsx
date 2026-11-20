import { useState } from "react";
import type { RosterDecision, TeamProposalView } from "../../../../shared/teams.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { TeamIcon } from "../InputPicker.js";
import { MEMBER_ROLE_LABELS } from "./team-labels.js";

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
  const [workspaces, setWorkspaces] = useState<Record<string, "shared" | "worktree">>(
    () => Object.fromEntries(plan.members.map((member) => [member.name, worktreeAvailable ? member.workspace ?? "shared" : "shared"])));
  const [autoWake, setAutoWake] = useState(true);
  const kept = plan.members.filter((member) => !removed.has(member.name));

  const toggle = (name: string) => setRemoved((previous) => {
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
        <h3 className="team-roster__objective" title={plan.objective}>{plan.objective}</h3>
        <span className="team-roster__count">{kept.length}/{plan.members.length}</span>
      </header>
      {plan.summary && <p className="team-roster__summary">{plan.summary}</p>}
      <ul className="team-roster__members">
        {plan.members.map((member) => {
          const off = removed.has(member.name);
          const tasks = (plan.tasks ?? []).filter((task) => task.owner === member.name);
          return (
            <li key={member.name} className={`team-roster__member${off ? " team-roster__member--removed" : ""}`}>
              <label className="team-roster__keep">
                <input type="checkbox" checked={!off} disabled={busy} aria-label={`保留 ${member.name}`}
                  onChange={() => toggle(member.name)} />
                <span className="team-roster__name">{member.name}</span>
                <span className="team-roster__role">{MEMBER_ROLE_LABELS[member.role ?? "worker"] ?? member.role}</span>
              </label>
              <select className="team-roster__workspace" aria-label={`${member.name} 的工作区`} disabled={busy || off || !worktreeAvailable}
                value={workspaces[member.name] ?? "shared"}
                onChange={(event) => setWorkspaces((previous) => ({ ...previous, [member.name]: event.target.value === "worktree" ? "worktree" : "shared" }))}>
                <option value="shared">共享工作区</option>
                <option value="worktree">独立 Worktree</option>
              </select>
              <p className="team-roster__brief">{member.brief}</p>
              {tasks.length > 0 && (
                <ul className="team-roster__tasks">
                  {tasks.map((task) => <li key={task.key}><code>{task.key}</code>{task.title}</li>)}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      {(plan.tasks ?? []).some((task) => !task.owner) && (
        <ul className="team-roster__tasks team-roster__tasks--open" aria-label="待领取任务">
          {(plan.tasks ?? []).filter((task) => !task.owner).map((task) => <li key={task.key}><code>{task.key}</code>{task.title}</li>)}
        </ul>
      )}
      <footer className="team-roster__footer">
        <span className="team-roster__settings">
          {modelLabel && <span>{modelLabel}</span>}
          <span>{APPROVAL_LABELS[approvalMode]}</span>
          <label className="team-roster__toggle">
            <input type="checkbox" checked={autoWake} disabled={busy} onChange={(event) => setAutoWake(event.target.checked)} />
            自动续跑
          </label>
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
