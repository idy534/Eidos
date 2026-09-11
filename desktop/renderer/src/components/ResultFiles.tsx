import { useMemo } from "react";
import { parseDiff } from "react-diff-view";
import type { Item, ToolCall } from "../contracts.js";
import { artifactPath, useArtifacts } from "./ArtifactContext.js";

/** References from persisted tool evidence; this is not an execution-success projection. */
export function toolFilePaths(call: ToolCall): string[] {
  const paths = new Set<string>();
  if (call.changeDiff) {
    try { for (const file of parseDiff(call.changeDiff)) for (const path of [file.oldPath, file.newPath]) if (path && path !== "/dev/null") paths.add(path); }
    catch { /* The original tool card still displays unsupported patch text. */ }
  }
  try {
    const result: unknown = JSON.parse(call.resultJson ?? "{}");
    const data: unknown = result && typeof result === "object" ? Reflect.get(result, "data") : undefined;
    if (data && typeof data === "object") for (const key of ["created", "modified", "deleted"]) {
      const values: unknown = Reflect.get(data, key);
      if (Array.isArray(values)) for (const path of values) if (typeof path === "string") paths.add(path);
    }
  } catch { /* Missing or malformed results do not create file references. */ }
  return [...paths];
}

export function ResultFiles({ items, incomplete }: { items: Item[]; incomplete: boolean }) {
  const actions = useArtifacts();
  const paths = useMemo(() => {
    if (!actions) return [];
    return [...new Set(items.flatMap((item) => item.toolCall ? toolFilePaths(item.toolCall) : []).map((path) => artifactPath(path, actions.executionRoot)).filter((path): path is string => Boolean(path)))];
  }, [items, actions?.executionRoot]);
  return <details className="result-files"><summary>结果文件 · {paths.length}</summary>
    <p>列表来自已加载的工具修改记录，可能包含计划修改或已删除文件。点击后打开当前文件。</p>
    {incomplete && <p>更早的记录尚未加载，完整目录见下方文件树。</p>}
    {paths.map((path) => <button type="button" key={path} title={path} onClick={() => actions?.openFile(path)}>{path}</button>)}
    {!paths.length && <p>当前没有已记录的结果文件。</p>}
  </details>;
}
