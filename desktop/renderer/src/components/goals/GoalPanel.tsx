import { useId, useState, type ReactNode } from "react";
import type { GoalView } from "../../../../shared/goals.js";
import type { Run } from "../../contracts.js";
import { ChevronDownIcon } from "../MenuIcons.js";
import { Tooltip } from "../Tooltip.js";
import { GoalBlockedIcon, GoalCheckIcon, GoalClearIcon, GoalClockIcon, GoalLoopIcon, GoalMarkIcon, GoalPauseIcon,
  GoalPausedIcon, GoalPlayIcon, GoalTokenIcon } from "./GoalIcons.js";
import "./goals.css";

interface Props {
  view: GoalView;
  ready: boolean;
  busy: boolean;
  error: string | undefined;
  activeRun: Run | undefined;
  onAction: (action: "pause" | "resume" | "clear") => void;
  onRefresh: () => void;
}

type Tone = "running" | "waiting" | "paused" | "blocked" | "complete";
const labels: Record<Tone, string> = { running: "进行中", waiting: "等待审批", paused: "已暂停续跑", blocked: "已受阻", complete: "已完成 · 模型声明" };
const icons: Record<Tone, ReactNode> = {
  running: <GoalMarkIcon />, waiting: <GoalMarkIcon />, paused: <GoalPausedIcon />, blocked: <GoalBlockedIcon />, complete: <GoalCheckIcon />,
};
const reasons: Record<string, string> = { idle_runs: "连续三轮没有新的有效工具结果。", runtime_restarted: "应用重启后，目标等待你继续。",
  run_failed: "上一轮执行失败，请查看执行记录。", run_stopped: "上一轮执行已停止。", run_canceled: "你停止了执行，目标已暂停续跑。",
  preparation_failed: "续跑准备失败，请检查模型和工作区。", workspace_rewound: "工作区已回退，目标等待你确认后继续。",
  plan_selected: "切换到 Plan 模式后，目标已暂停续跑。", user_paused: "你暂停了续跑，当前执行会继续完成。",
  legacy_budget_removed: "旧版预算功能已移除，目标等待你手动继续。" };

function compact(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

function duration(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "<1 分钟";
  return minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

export function GoalPanel({ view, ready, busy, error, activeRun, onAction, onRefresh }: Props) {
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();
  const goal = view.goal;
  if (!goal || goal.status === "cleared") return error ? <p className="goal-card__error goal-card__error--alone" role="alert">{error}</p> : null;
  const waiting = goal.status === "active" && (activeRun?.status === "waiting_approval" || activeRun?.status === "waiting_input");
  const tone: Tone = goal.status === "active" ? waiting ? "waiting" : "running" : goal.status;
  const label = activeRun?.status === "waiting_input" && tone === "waiting" ? "等待回答" : labels[tone];
  const tokens = view.tokensUsed ?? 0;
  const tokenTitle = `${view.usageIncomplete ? "已知 " : ""}${tokens.toLocaleString()} Token${view.usageIncomplete ? "，部分请求未返回完整用量" : ""}`;
  const reason = goal.statusReason ? reasons[goal.statusReason] : undefined;
  const disabled = !ready || busy;
  return <section className="goal-card" aria-label="目标执行状态" data-tone={tone} data-expanded={expanded || undefined}>
    <div className="goal-card__bar">
      <button type="button" className="goal-card__summary" aria-expanded={expanded} aria-controls={bodyId}
        aria-label={`目标 · ${label}`} onClick={() => setExpanded((value) => !value)}>
        <span className="goal-card__state" aria-hidden="true">{icons[tone]}</span>
        <span className="goal-card__objective">{goal.objective}</span>
        {tone !== "running" && <span className="goal-card__chip">{label}</span>}
      </button>
      <span className="goal-card__metrics">
        <Tooltip content={`已执行 ${view.runCount ?? 0} 轮`}><span className="goal-card__metric" tabIndex={0}><GoalLoopIcon />{view.runCount ?? 0}</span></Tooltip>
        <Tooltip content={tokenTitle}><span className="goal-card__metric goal-card__metric--wide" tabIndex={0}><GoalTokenIcon />{view.usageIncomplete ? "≥" : ""}{compact(tokens)}</span></Tooltip>
        <Tooltip content={`有效执行 ${duration(view.effectiveMs ?? 0)}`}><span className="goal-card__metric goal-card__metric--wide" tabIndex={0}><GoalClockIcon />{duration(view.effectiveMs ?? 0)}</span></Tooltip>
      </span>
      <span className="goal-card__actions">
        {goal.status === "active" && <Tooltip content="暂停续跑：当前执行会继续完成">
          <button type="button" className="goal-card__icon-button" aria-label="暂停续跑" disabled={disabled} onClick={() => onAction("pause")}><GoalPauseIcon /></button>
        </Tooltip>}
        {(goal.status === "paused" || goal.status === "blocked") && <Tooltip content="继续目标">
          <button type="button" className="goal-card__icon-button goal-card__icon-button--primary" aria-label="继续目标" disabled={disabled} onClick={() => onAction("resume")}><GoalPlayIcon /></button>
        </Tooltip>}
        <Tooltip content={expanded ? "收起" : "展开详情"}>
          <button type="button" className="goal-card__icon-button goal-card__chevron" aria-label={expanded ? "收起目标详情" : "展开目标详情"}
            aria-expanded={expanded} aria-controls={bodyId} onClick={() => setExpanded((value) => !value)}><ChevronDownIcon /></button>
        </Tooltip>
      </span>
    </div>
    {busy && <span className="goal-card__sr" role="status">正在保存目标…</span>}
    {expanded && <div className="goal-card__body" id={bodyId}>
      {(reason || goal.statusDetail) && <div className="goal-card__note" data-tone={tone}>
        <span aria-hidden="true">{icons[tone]}</span>
        <div>{reason && <p>{reason}</p>}{goal.statusDetail && <p className="goal-card__detail">{goal.statusDetail}</p>}</div>
      </div>}
      {goal.evidence?.length ? <ul className="goal-card__evidence" aria-label="完成证据">
        {goal.evidence.map((entry, index) => <li key={index}><GoalCheckIcon /><div><strong>{entry.requirement}</strong><p>{entry.proof}</p></div></li>)}
      </ul> : null}
      <div className="goal-card__footer">
        <span>{view.usageIncomplete ? "部分请求未返回完整用量" : null}</span>
        <button type="button" className="goal-card__clear" disabled={disabled} onClick={() => onAction("clear")}><GoalClearIcon />清除目标</button>
      </div>
    </div>}
    {error && <div className="goal-card__error" role="alert"><span>{error}</span>
      <button type="button" className="goal-card__clear" disabled={!ready} onClick={onRefresh}>重新读取</button></div>}
  </section>;
}
