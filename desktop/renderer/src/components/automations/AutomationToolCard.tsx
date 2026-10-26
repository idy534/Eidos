import { isAutomationResponse } from "../../../../shared/automation.js";
import { AutomationsIcon } from "../MenuIcons.js";
import { formatAutomationTime } from "./automation-time.js";

export function AutomationToolCard({ response, mode, schedule }: { response: unknown; mode: unknown; schedule?: unknown }) {
  if (!isAutomationResponse(response)) {
    return (
      <div className="tool-item tool-item--read-done">
        <span className="tool-icon" aria-hidden="true">
          <AutomationsIcon />
        </span>
        <span>定时任务操作已记录，请在定时任务页面查看结果。</span>
      </div>
    );
  }
  const task = response.automation;
  const timezone = task?.schedule.timezone ?? (schedule && typeof schedule === "object" && "timezone" in schedule
    && typeof schedule.timezone === "string" ? schedule.timezone : "UTC");
  const modeKey = typeof mode === "string" ? mode : "";
  const effectiveMode = modeKey || (
    response.kind === "preview"
      ? "preview"
      : response.kind === "list"
      ? "view"
      : response.kind === "mutation"
      ? (response.changed ? "create" : "update")
      : ""
  );
  const label = ({
    create: "已创建定时任务",
    update: "已更新定时任务",
    delete: "已删除定时任务",
    view: "已查看定时任务",
    preview: "执行时间预览",
  } as Record<string, string>)[effectiveMode] ?? "定时任务";

  return (
    <details className="tool-item">
      <summary>
        <span className="tool-icon" aria-hidden="true">
          <AutomationsIcon />
        </span>
        <span className="tool-summary-text">
          {label}{task ? ` · ${task.name}` : ""}
        </span>
      </summary>
      <div className="tool-body">
        {task && (
          <>
            <p>
              {task.status === "ACTIVE" ? "已启用" : "已暂停"} · {task.approvalMode === "full_access" ? "完全访问" : task.approvalMode === "auto_review" ? "替我审批" : "请求审批"} · {task.target.kind === "existing_session" ? "已有会话" : "每次新建会话"}
            </p>
            <p>
              {task.schedule.runAt ? formatAutomationTime(task.schedule.runAt, timezone) : `${task.schedule.expression} · ${timezone}`}
            </p>
          </>
        )}
        {response.nextRunAt && <p>下次执行：{formatAutomationTime(response.nextRunAt, timezone)}</p>}
        {response.kind === "list" && (
          <ul>
            {response.items?.map((item) => (
              <li key={item.id}>
                {item.name} · {item.status === "ACTIVE" ? "已启用" : "已暂停"}
              </li>
            ))}
          </ul>
        )}
        {response.kind === "preview" && (
          <ul>
            {response.nextRuns?.map((time) => (
              <li key={time}>{formatAutomationTime(time, timezone)}</li>
            ))}
          </ul>
        )}
        {response.replayed && <p>这是先前已保存操作的回执。</p>}
      </div>
    </details>
  );
}
