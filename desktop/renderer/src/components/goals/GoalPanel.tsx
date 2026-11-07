import { useState } from "react";
import type { GoalView } from "../../../../shared/goals.js";
import type { Run } from "../../contracts.js";
import { Button } from "../Button.js";
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
const labels = { active: "进行中", paused: "已暂停续跑", blocked: "已受阻", complete: "已完成 · 模型声明", cleared: "已清除" };
const reasons: Record<string, string> = { idle_runs: "连续三轮没有新的有效工具结果。", runtime_restarted: "应用重启后，目标等待你继续。",
  run_failed: "上一轮执行失败，请查看执行记录。", run_stopped: "上一轮执行已停止。", run_canceled: "你停止了执行，目标已暂停续跑。",
  preparation_failed: "续跑准备失败，请检查模型和工作区。", workspace_rewound: "工作区已回退，目标等待你确认后继续。",
  legacy_budget_removed: "旧版预算功能已移除，目标等待你手动继续。" };

export function GoalPanel({ view, ready, busy, error, activeRun, onAction, onRefresh }: Props) {
  const [expanded, setExpanded] = useState(false);
  const goal = view.goal;
  if (!goal || goal.status === "cleared") return error ? <p className="goal-panel__error" role="alert">{error}</p> : null;
  const status = goal.status === "active" && activeRun?.status === "waiting_approval" ? "等待审批"
    : goal.status === "active" && activeRun?.status === "waiting_input" ? "等待回答" : labels[goal.status];
  const metrics = `${view.runCount ?? 0} 轮 · ${view.usageIncomplete ? "已知 " : ""}${(view.tokensUsed ?? 0).toLocaleString()} Token · ${Math.floor((view.effectiveMs ?? 0) / 60000)} 分钟`;
  return <section className="goal-panel" aria-label="目标执行状态" data-status={goal.status}>
    <div className="goal-panel__header"><button type="button" className="goal-panel__toggle" aria-expanded={expanded}
      onClick={() => setExpanded((value) => !value)}><span aria-hidden="true">◎</span><strong aria-live="polite">目标 · {status}</strong><span aria-hidden="true">{expanded ? "⌃" : "⌄"}</span></button>
      <span className="goal-panel__metrics">{metrics}</span>
      {goal.status === "active" && <Button variant="ghost" size="small" disabled={!ready || busy} title="当前执行会继续完成；目标不再自动续跑。"
        onClick={() => onAction("pause")}>暂停续跑</Button>}
      {["paused", "blocked"].includes(goal.status) && <Button variant="ghost" size="small" disabled={!ready || busy}
        onClick={() => onAction("resume")}>继续目标</Button>}
    </div>
    {expanded && <div className="goal-panel__body">
      <p className="goal-panel__objective">{goal.objective}</p><p className="goal-panel__hint">{metrics}{view.usageIncomplete ? " · 部分请求未返回完整用量" : ""}</p>
      {goal.statusReason && reasons[goal.statusReason] && <p className="goal-panel__hint">{reasons[goal.statusReason]}</p>}
      {goal.statusDetail && <p className="goal-panel__detail">{goal.statusDetail}</p>}
      {goal.evidence?.length ? <ul className="goal-panel__evidence">{goal.evidence.map((entry, index) => <li key={index}><strong>{entry.requirement}</strong><p>{entry.proof}</p></li>)}</ul> : null}
      <div className="goal-panel__actions">
        <Button variant="ghost" size="small" disabled={!ready || busy} onClick={() => onAction("clear")}>清除目标</Button></div>
    </div>}
    {busy && <span className="goal-panel__hint" role="status">正在保存目标…</span>}
    {error && <div className="goal-panel__error" role="alert">{error}<Button variant="ghost" size="small" disabled={!ready} onClick={onRefresh}>重新读取</Button></div>}
  </section>;
}
