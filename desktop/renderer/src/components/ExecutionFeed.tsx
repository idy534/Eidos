import type { Item, Run } from "../contracts";


export function ExecutionFeed({ items, runs }: { items: Item[]; runs: Run[] }) {
  if (items.length === 0) {
    return (
      <div className="feed-empty" role="status">
        <p>这个 Session 还没有执行记录。</p>
      </div>
    );
  }
  return (
    <section className="feed" aria-label="Execution Feed" aria-live="polite">
      {items.map((item) => <FeedItem key={item.id} item={item} />)}
      {runs.at(-1)?.status === "failed" && (
        <p className="run-error" role="alert">Run 失败：{runs.at(-1)?.errorCode ?? "UNKNOWN_ERROR"}</p>
      )}
    </section>
  );
}

function FeedItem({ item }: { item: Item }) {
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
  if (item.kind === "tool_call" && item.toolCall) {
    return (
      <details className="tool-item" open={item.status === "in_progress"}>
        <summary>
          <span className="tool-icon" aria-hidden="true">›_</span>
          <span>{toolLabel(item.toolCall.toolName)}</span>
          <small>{statusLabel(item.status)}</small>
        </summary>
        <div className="tool-body">
          <code>{item.toolCall.argumentsJson}</code>
          {item.toolCall.resultJson && <pre>{prettyJson(item.toolCall.resultJson)}</pre>}
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
