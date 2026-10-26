import { useId } from "react";
import type { AutomationSummary } from "../../../../shared/automation.js";

interface Props {
  items: AutomationSummary[];
  searchQuery: string;
  selectedId?: string | undefined;
  busy: boolean;
  hasMore: boolean;
  onSelect: (id: string) => void;
}

function isUpcoming(task: AutomationSummary): boolean {
  return task.status === "ACTIVE" && !(task.kind === "at" && task.pauseReason === "one_shot_fired");
}

function formatScheduleSummary(task: AutomationSummary): string {
  let datePart = "暂无执行";
  if (task.kind === "at" && task.pauseReason === "one_shot_fired") {
    datePart = "已结束";
  } else if (task.status === "PAUSED") {
    datePart = "已暂停";
  } else if (task.nextRunAt) {
    datePart = new Date(task.nextRunAt).toLocaleDateString("zh-CN", {
      timeZone: task.schedule?.timezone || "UTC", month: "numeric", day: "numeric",
    });
  }

  let freqPart = "定时";
  if (task.kind === "cron" && task.schedule?.expression) {
    const expr = task.schedule.expression.trim();
    if (expr === "0 9 * * *") freqPart = "每天";
    else if (expr.endsWith("* * 1-5") || expr.endsWith("* * 1,2,3,4,5")) freqPart = "工作日";
    else if (expr.includes("* * 0") || expr.includes("* * 1") || expr.includes("* * 5")) freqPart = "每周";
    else if (expr.startsWith("*/10")) freqPart = "每10分钟";
    else if (expr.startsWith("*/30")) freqPart = "每30分钟";
    else if (expr.startsWith("0 *")) freqPart = "每小时";
    else freqPart = "周期";
  } else if (task.kind === "at") {
    freqPart = "一次性";
  }
  return `${datePart} · ${freqPart} · ${task.schedule?.timezone || "UTC"}`;
}

export function AutomationTaskList({ items, searchQuery, selectedId, busy, hasMore, onSelect }: Props) {
  const id = useId();
  const query = searchQuery.trim().toLowerCase();
  const visible = items.filter((task) => !query || task.name.toLowerCase().includes(query));
  const groups = [
    { id: "upcoming", title: "即将执行", items: visible.filter(isUpcoming), empty: "暂无即将执行的任务。" },
    { id: "inactive", title: "已完成与暂停", items: visible.filter((task) => !isUpcoming(task)), empty: "暂无已完成或暂停的任务。" },
  ];

  return groups.map((group) => (
    <section className="automation-task-group" aria-labelledby={`${id}-${group.id}`} key={group.id}>
      <div className="automation-section-header">
        <h3 className="automation-section-title" id={`${id}-${group.id}`}>{group.title}</h3>
      </div>
      {group.items.length ? group.items.map((task) => (
        <button
          type="button"
          disabled={busy}
          aria-pressed={selectedId === task.id}
          className={`automation-task-item${selectedId === task.id ? " selected" : ""}`}
          key={task.id}
          onClick={() => onSelect(task.id)}
        >
          <span className="automation-task-item-main">
            <strong className="automation-task-name">{task.name}</strong>
            <span className="automation-task-schedule">{formatScheduleSummary(task)}</span>
          </span>
          {isUpcoming(task) && <span className="automation-active-dot" aria-hidden="true" />}
        </button>
      )) : (
        <p className="automation-empty-hint">
          {hasMore ? "当前已加载的任务中没有匹配项，可继续加载更多。" : query ? "没有匹配的任务。" : group.empty}
        </p>
      )}
    </section>
  ));
}
