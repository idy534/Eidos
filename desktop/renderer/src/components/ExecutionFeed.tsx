import { Fragment } from "react";

import type { ApprovalRequest, Item, Run } from "../contracts";
import { terminalRunPresentation } from "../session-state";


interface Props {
  items: Item[];
  runs: Run[];
  approvals: ApprovalRequest[];
  disabled: boolean;
  onApproval: (request: ApprovalRequest, decision: "approve" | "reject") => void;
}

export function ExecutionFeed({ items, runs, approvals, disabled, onApproval }: Props) {
  if (items.length === 0) {
    return (
      <div className="feed-empty" role="status">
        <p>这个 Session 还没有执行记录。</p>
      </div>
    );
  }
  const runsById = new Map(runs.map((run) => [run.id, run]));
  const lastItemByRun = new Map<string, string>();
  for (const item of items) {
    lastItemByRun.set(item.runId, item.id);
  }
  return (
    <section className="feed" aria-label="Execution Feed" aria-live="polite">
      {items.map((item) => {
        const run = runsById.get(item.runId);
        return (
          <Fragment key={item.id}>
            <FeedItem
              item={item}
              run={run}
              approval={approvals.find((request) => request.itemId === item.id)}
              disabled={disabled}
              onApproval={onApproval}
            />
            {run && lastItemByRun.get(item.runId) === item.id && <RunOutcome run={run} />}
          </Fragment>
        );
      })}
    </section>
  );
}

function RunOutcome({ run }: { run: Run }) {
  const presentation = terminalRunPresentation(run);
  const active = presentation ?? activeRunPresentation(run);
  if (!active) return null;
  return (
    <p
      className={`run-status run-status--${active.tone}`}
      role={active.tone === "error" ? "alert" : "status"}
    >
      {active.label}
      {run.sideEffectsMayExist && "。副作用结果可能存在，下一步必须先只读核验"}
    </p>
  );
}

function activeRunPresentation(run: Run) {
  switch (run.status) {
    case "queued": return { label: "Run 已排队", tone: "neutral" as const };
    case "running": return { label: "Run 正在执行", tone: "neutral" as const };
    case "waiting_approval": return { label: "Run 等待批准", tone: "warning" as const };
    case "waiting_user_input": return {
      label: `Run 已暂停：${pauseLabel(run.pauseReason)}`, tone: "warning" as const,
    };
    case "finalizing": return { label: "Run 正在生成最终说明", tone: "neutral" as const };
    default: return undefined;
  }
}

function pauseLabel(reason: string | undefined): string {
  return ({
    repeated_approval_rejection: "连续拒绝，请补充新的处理方式",
    model_stream_interrupted: "模型输出中断，请确认后继续",
    sensitive_scan_failed: "安全扫描未完成，原文未展示",
    repeated_sensitive_tool_input: "模型重复生成受保护参数，请改写任务",
    reconciliation_required: "需要先核验可能发生的副作用",
    segment_step_limit: "本段已达到步骤上限",
    segment_time_limit: "本段已达到时间上限",
  } as Record<string, string>)[reason ?? ""] ?? "需要你的输入才能继续";
}

function FeedItem({
  item,
  run,
  approval,
  disabled,
  onApproval,
}: {
  item: Item;
  run: Run | undefined;
  approval: ApprovalRequest | undefined;
  disabled: boolean;
  onApproval: (request: ApprovalRequest, decision: "approve" | "reject") => void;
}) {
  if (item.kind === "user_message") {
    return <article className="feed-item feed-item--user"><p>{item.content}</p></article>;
  }
  if (item.kind === "assistant_message") {
    return (
      <article className="feed-item feed-item--assistant">
        <p className="feed-label">Eidos</p>
        <p className={item.status === "in_progress" ? "streaming" : ""}>
          {item.content || "正在思考下一步…"}
        </p>
      </article>
    );
  }
  if (["tool_call", "file_change", "command_execution"].includes(item.kind) && item.toolCall) {
    if (approval) {
      return (
        <article className="approval-card" aria-labelledby={`approval-${approval.id}`}>
          <div className="approval-heading">
            <div>
              <p className="feed-label">需要你的批准</p>
              <h3 id={`approval-${approval.id}`}>{approval.summary}</h3>
            </div>
            <span>{approval.kind === "file_change" ? "文件变更" : "Shell 命令"}</span>
          </div>
          <pre className="diff-view">
            {approval.kind === "file_change"
              ? approval.diff
              : `$ ${approval.command}\n\ncwd: ${approval.cwd}\nnetwork: disabled\ntimeout: ${approval.timeoutSeconds}s`}
          </pre>
          <div className="approval-actions">
            <button className="button-secondary" disabled={disabled || !run?.allowedActions?.includes("reject")} onClick={() => onApproval(approval, "reject")}>拒绝</button>
            <button disabled={disabled || !run?.allowedActions?.includes("approve")} onClick={() => onApproval(approval, "approve")}>{approval.kind === "file_change" ? "批准并写入" : "批准并运行"}</button>
          </div>
        </article>
      );
    }
    return (
      <details className="tool-item" open={item.status === "in_progress"}>
        <summary>
          <span className="tool-icon" aria-hidden="true">›_</span>
          <span>{toolLabel(item.toolCall.toolName)}</span>
          <small>{statusLabel(item.status)}</small>
        </summary>
        <div className="tool-body">
          <p>{safeToolSummary(item.toolCall.resultJson, item.status)}</p>
        </div>
      </details>
    );
  }
  return null;
}

function toolLabel(name: string): string {
  return ({ list_files: "列出文件", read_file: "读取文件", read_file_range: "读取文件范围", search_text: "搜索文本", write_file: "写入文件", apply_patch: "应用补丁", delete_file: "删除文件", run_shell: "运行命令" } as Record<string, string>)[name] ?? "工具操作";
}

function statusLabel(status: Item["status"]): string {
  return ({ in_progress: "执行中", completed: "完成", failed: "失败", declined: "已拒绝", canceled: "已取消" } as const)[status];
}

function safeToolSummary(value: string | undefined, status: Item["status"]): string {
  if (!value) return statusLabel(status);
  try {
    const parsed = JSON.parse(value) as { summary?: unknown; code?: unknown };
    return typeof parsed.summary === "string"
      ? parsed.summary
      : typeof parsed.code === "string" ? parsed.code : statusLabel(status);
  } catch {
    return statusLabel(status);
  }
}
