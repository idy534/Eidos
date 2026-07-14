import type { ApprovalRequest, Item, Run } from "../contracts";


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
  return (
    <section className="feed" aria-label="Execution Feed" aria-live="polite">
      {items.map((item) => (
        <FeedItem
          key={item.id}
          item={item}
          approval={approvals.find((request) => request.itemId === item.id)}
          disabled={disabled}
          onApproval={onApproval}
        />
      ))}
      {runs.at(-1)?.status === "failed" && (
        <p className="run-error" role="alert">Run 失败：{runs.at(-1)?.errorCode ?? "UNKNOWN_ERROR"}</p>
      )}
    </section>
  );
}

function FeedItem({
  item,
  approval,
  disabled,
  onApproval,
}: {
  item: Item;
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
            <button className="button-secondary" disabled={disabled} onClick={() => onApproval(approval, "reject")}>拒绝</button>
            <button disabled={disabled} onClick={() => onApproval(approval, "approve")}>{approval.kind === "file_change" ? "批准并写入" : "批准并运行"}</button>
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
          {item.toolCall.argumentsJson && <code>{item.toolCall.argumentsJson}</code>}
          {item.kind === "command_execution" && item.content && <pre>{item.content}</pre>}
          {item.toolCall.resultJson && <pre>{prettyJson(item.toolCall.resultJson)}</pre>}
          {item.toolCall.approvalDiff && <pre>{item.toolCall.approvalDiff}</pre>}
        </div>
      </details>
    );
  }
  return null;
}

function toolLabel(name: string): string {
  return ({ list_files: "列出文件", read_file: "读取文件", search_text: "搜索文本" } as Record<string, string>)[name] ?? name;
}

function statusLabel(status: Item["status"]): string {
  return ({ in_progress: "执行中", completed: "完成", failed: "失败", declined: "已拒绝", canceled: "已取消" } as const)[status];
}

function prettyJson(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}
