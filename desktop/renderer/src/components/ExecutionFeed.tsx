import { Fragment, useEffect, useState } from "react";
import type { ReactNode } from "react";

import type { ApprovalRequest, Item, Run, ToolCall } from "../contracts.js";
import { terminalRunPresentation } from "../session-state.js";
import { MarkdownContent } from "./MarkdownContent.js";


interface Props {
  items: Item[];
  runs: Run[];
  approvals: ApprovalRequest[];
  disabled: boolean;
  onApproval: (request: ApprovalRequest, decision: "approve" | "reject") => void;
}

interface Segment {
  user: Item | undefined;
  process: Item[];
  response: Item[];
}

const ACTIVE_RUN_STATUSES = new Set<Run["status"]>([
  "queued", "running", "waiting_approval", "waiting_user_input", "finalizing",
]);

const TERMINAL_RUN_STATUSES = new Set<Run["status"]>([
  "stopped", "succeeded", "failed", "canceled", "interrupted",
]);


export function ExecutionFeed({ items, runs, approvals, disabled, onApproval }: Props) {
  if (items.length === 0) {
    return (
      <div className="feed-empty" role="status">
        <p>这个 Session 还没有执行记录。</p>
      </div>
    );
  }

  const runsById = new Map(runs.map((run) => [run.id, run]));
  const itemGroups = groupItemsByRun(items);

  return (
    <section className="feed" aria-label="Execution Feed" aria-live="polite">
      {itemGroups.map(({ runId, items: runItems }) => {
        const run = runsById.get(runId);
        if (!run) return null;
        const segments = splitRunIntoSegments(runItems);
        return (
          <Fragment key={runId}>
            {segments.map((segment, index) => (
              <RunSegment
                key={`${runId}:${segment.user?.id ?? index}`}
                segment={segment}
                run={run}
                isLast={index === segments.length - 1}
                approvals={approvals}
                disabled={disabled}
                onApproval={onApproval}
              />
            ))}
            <RunNotice run={run} />
          </Fragment>
        );
      })}
    </section>
  );
}

function RunSegment({
  segment,
  run,
  isLast,
  approvals,
  disabled,
  onApproval,
}: {
  segment: Segment;
  run: Run;
  isLast: boolean;
  approvals: ApprovalRequest[];
  disabled: boolean;
  onApproval: Props["onApproval"];
}) {
  const showThinking = isLast
    && ACTIVE_RUN_STATUSES.has(run.status)
    && segment.process.length === 0
    && segment.response.length === 0;

  return (
    <>
      {segment.user && <UserMessage item={segment.user} />}
      {segment.process.length > 0 && (
        <ProcessGroup
          key={`${run.id}:${TERMINAL_RUN_STATUSES.has(run.status) ? "done" : "active"}`}
          run={run}
        >
          {segment.process.map((item) => (
            <ProcessItem
              key={item.id}
              item={item}
              run={run}
              approval={approvals.find((request) => request.itemId === item.id)}
              disabled={disabled}
              onApproval={onApproval}
            />
          ))}
        </ProcessGroup>
      )}
      {showThinking && <p className="thinking-indicator" role="status">正在思考</p>}
      {segment.response.map((item) => <AssistantMessage key={item.id} item={item} />)}
    </>
  );
}

function ProcessGroup({ run, children }: { run: Run; children: ReactNode }) {
  const terminal = TERMINAL_RUN_STATUSES.has(run.status);
  const [open, setOpen] = useState(!terminal);
  return (
    <details className="process-group" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary><ProcessLabel run={run} /></summary>
      <div className="process-content">{children}</div>
    </details>
  );
}

function UserMessage({ item }: { item: Item }) {
  return <article className="feed-item feed-item--user"><p>{item.content}</p></article>;
}

function AssistantMessage({ item }: { item: Item }) {
  return (
    <article className="feed-item feed-item--assistant">
      <div className={item.status === "in_progress" ? "streaming" : ""}>
        <MarkdownContent content={item.content || ""} />
      </div>
    </article>
  );
}

function ProcessItem({
  item,
  run,
  approval,
  disabled,
  onApproval,
}: {
  item: Item;
  run: Run;
  approval: ApprovalRequest | undefined;
  disabled: boolean;
  onApproval: Props["onApproval"];
}) {
  if (item.kind === "assistant_message") {
    if (!item.content) return null;
    return <div className="process-text"><MarkdownContent content={item.content} /></div>;
  }
  if (!item.toolCall) return null;

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
          <button className="button-secondary" disabled={disabled || !run.allowedActions?.includes("reject")} onClick={() => onApproval(approval, "reject")}>拒绝</button>
          <button disabled={disabled || !run.allowedActions?.includes("approve")} onClick={() => onApproval(approval, "approve")}>{approval.kind === "file_change" ? "批准并写入" : "批准并运行"}</button>
        </div>
      </article>
    );
  }

  return item.toolCall.toolName === "run_shell"
    ? <ShellItem item={item} toolCall={item.toolCall} />
    : <ToolItem item={item} toolCall={item.toolCall} />;
}

function ShellItem({ item, toolCall }: { item: Item; toolCall: ToolCall }) {
  const args = parseObject(toolCall.argumentsJson);
  const result = parseObject(toolCall.resultJson);
  const data = objectField(result, "data");
  const command = stringField(args, "command") || stringArrayField(args, "argv").join(" ") || "Shell 命令";
  const stdout = stringField(data, "stdout");
  const stderr = stringField(data, "stderr");
  const exitCode = numberField(data, "exitCode");
  const success = item.status === "completed" && (exitCode === undefined || exitCode === 0);
  const [open, setOpen] = useState(item.status === "in_progress");

  useEffect(() => {
    if (item.status !== "in_progress") setOpen(false);
  }, [item.status]);

  return (
    <details className="tool-item tool-item--shell" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="tool-icon tool-icon--terminal" aria-hidden="true">›_</span>
        <span>{shellSummary(item.status, command)}</span>
      </summary>
      <div className="shell-result">
        <p className="shell-label">Shell</p>
        <pre className="shell-command"><span aria-hidden="true">$ </span>{command}</pre>
        {stdout && <pre className="shell-output">{stdout}</pre>}
        {stderr && <pre className="shell-output shell-output--error">{stderr}</pre>}
        {!stdout && !stderr && <p className="shell-empty">无输出</p>}
        <p className={`shell-status ${success ? "shell-status--success" : "shell-status--error"}`}>
          {item.status === "in_progress" ? "运行中" : success ? "✓ 成功" : statusLabel(item.status)}
        </p>
      </div>
    </details>
  );
}

function ToolItem({ item, toolCall }: { item: Item; toolCall: ToolCall }) {
  const [open, setOpen] = useState(item.status === "in_progress");
  useEffect(() => {
    if (item.status !== "in_progress") setOpen(false);
  }, [item.status]);
  return (
    <details className="tool-item" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="tool-icon" aria-hidden="true">{toolIcon(toolCall.toolName)}</span>
        <span>{toolSummary(toolCall, item.status)}</span>
      </summary>
      <div className="tool-body"><p>{safeToolSummary(toolCall.resultJson, item.status)}</p></div>
    </details>
  );
}

function ProcessLabel({ run }: { run: Run }) {
  const now = useCurrentTime(!TERMINAL_RUN_STATUSES.has(run.status));
  const duration = Math.max(0, (run.completedAt ?? now) - (run.startedAt ?? run.createdAt));
  const prefix = TERMINAL_RUN_STATUSES.has(run.status)
    ? "已处理"
    : run.status === "waiting_user_input" ? "处理已暂停" : "正在处理";
  return <span>{prefix} {formatDuration(duration)}</span>;
}

function RunNotice({ run }: { run: Run }) {
  if (run.status === "succeeded") return null;
  const presentation = terminalRunPresentation(run);
  const active = presentation ?? activeRunPresentation(run);
  if (!active || ["queued", "running", "finalizing"].includes(run.status)) return null;
  return (
    <p className={`run-notice run-notice--${active.tone}`} role={active.tone === "error" ? "alert" : "status"}>
      {active.label}
      {run.sideEffectsMayExist && "。副作用结果可能存在，下一步必须先只读核验"}
    </p>
  );
}

function groupItemsByRun(items: Item[]): Array<{ runId: string; items: Item[] }> {
  const groups: Array<{ runId: string; items: Item[] }> = [];
  const byRun = new Map<string, Item[]>();
  for (const item of items) {
    let group = byRun.get(item.runId);
    if (!group) {
      group = [];
      byRun.set(item.runId, group);
      groups.push({ runId: item.runId, items: group });
    }
    group.push(item);
  }
  return groups;
}

function splitRunIntoSegments(items: Item[]): Segment[] {
  const sourceSegments: Item[][] = [];
  for (const item of items) {
    if (item.kind === "user_message" || sourceSegments.length === 0) {
      sourceSegments.push([]);
    }
    const current = sourceSegments.at(-1);
    if (current) current.push(item);
  }
  return sourceSegments.map((segmentItems) => {
    const user = segmentItems.find((item) => item.kind === "user_message");
    const body = segmentItems.filter((item) => item.kind !== "user_message");
    const tools = body.filter((item) => item.kind !== "assistant_message");
    if (tools.length === 0) {
      return { user, process: [], response: body.filter((item) => item.kind === "assistant_message") };
    }
    const lastToolOrdinal = Math.max(...tools.map((item) => item.ordinal));
    return {
      user,
      process: body.filter((item) => item.kind !== "assistant_message" || item.ordinal <= lastToolOrdinal),
      response: body.filter((item) => item.kind === "assistant_message" && item.ordinal > lastToolOrdinal),
    };
  });
}

function activeRunPresentation(run: Run) {
  switch (run.status) {
    case "waiting_approval": return { label: "等待批准", tone: "warning" as const };
    case "waiting_user_input": return { label: `已暂停：${pauseLabel(run.pauseReason)}`, tone: "warning" as const };
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

function shellSummary(status: Item["status"], command: string): string {
  const compact = command.replace(/\s+/g, " ").trim();
  const visible = compact.length > 96 ? `${compact.slice(0, 95)}…` : compact;
  if (status === "in_progress") return `正在运行 ${visible}`;
  if (status === "completed") return `已运行 ${visible}`;
  return `${statusLabel(status)} ${visible}`;
}

function toolSummary(toolCall: ToolCall, status: Item["status"]): string {
  const args = parseObject(toolCall.argumentsJson);
  const path = stringField(args, "path") || stringField(args, "filePath");
  const query = stringField(args, "query") || stringField(args, "pattern");
  const running = status === "in_progress";
  const labels: Record<string, string> = {
    list_files: running ? "正在列出文件" : "已列出文件",
    read_file: running ? `正在读取 ${path || "文件"}` : `已读取 ${path || "文件"}`,
    read_file_range: running ? `正在读取 ${path || "文件"}` : `已读取 ${path || "文件"}`,
    search_text: running ? `正在搜索 ${query || "文本"}` : `已搜索 ${query || "文本"}`,
    write_file: running ? `正在编辑 ${path || "文件"}` : `已编辑 ${path || "文件"}`,
    apply_patch: running ? `正在编辑 ${path || "文件"}` : `已编辑 ${path || "文件"}`,
    delete_file: running ? `正在删除 ${path || "文件"}` : `已删除 ${path || "文件"}`,
  };
  return labels[toolCall.toolName] ?? `${running ? "正在运行" : "已运行"} ${toolCall.toolName}`;
}

function toolIcon(name: string): string {
  if (["read_file", "read_file_range", "list_files"].includes(name)) return "▱";
  if (name === "search_text") return "⌕";
  if (["write_file", "apply_patch", "delete_file"].includes(name)) return "✎";
  return "◇";
}

function statusLabel(status: Item["status"]): string {
  return ({ in_progress: "运行中", completed: "成功", failed: "失败", declined: "已拒绝", canceled: "已取消" } as const)[status];
}

function safeToolSummary(value: string | undefined, status: Item["status"]): string {
  const parsed = parseObject(value);
  return stringField(parsed, "summary") || stringField(parsed, "code") || statusLabel(status);
}

function parseObject(value: string | undefined): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed: unknown = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function objectField(source: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = source[key];
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringField(source: Record<string, unknown>, key: string): string {
  return typeof source[key] === "string" ? source[key] : "";
}

function stringArrayField(source: Record<string, unknown>, key: string): string[] {
  return Array.isArray(source[key])
    ? source[key].filter((value): value is string => typeof value === "string")
    : [];
}

function numberField(source: Record<string, unknown>, key: string): number | undefined {
  return typeof source[key] === "number" ? source[key] : undefined;
}

function formatDuration(durationMs: number): string {
  const seconds = Math.max(0, Math.floor(durationMs / 1_000));
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return minutes > 0 ? `${minutes}m ${remaining}s` : `${remaining}s`;
}

function useCurrentTime(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [live]);
  return now;
}
