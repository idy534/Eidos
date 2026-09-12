import { useMemo } from "react";
import { parseDiff } from "react-diff-view";
import type { Item, ToolCall } from "../contracts.js";
import { artifactPath, useArtifacts } from "./ArtifactContext.js";

/** References from persisted tool evidence; this is not an execution-success projection. */
export function toolFileChanges(call: ToolCall): { path: string; deleted: boolean; observed: boolean }[] {
  const paths = new Map<string, { path: string; deleted: boolean; observed: boolean }>();
  const observed = ["run_shell", "write_stdin", "shell"].includes(call.toolName);
  const add = (path: unknown, deleted = false) => {
    if (typeof path === "string" && path && path !== "/dev/null") paths.set(path, { path, deleted, observed });
  };
  if (call.changeDiff) {
    try { for (const file of parseDiff(call.changeDiff)) {
      if (file.oldPath !== file.newPath && file.oldPath !== "/dev/null") add(file.oldPath, true);
      add(file.newPath === "/dev/null" ? file.oldPath : file.newPath, file.type === "delete");
    } }
    catch { /* The original tool card still displays unsupported patch text. */ }
  }
  try {
    const result: unknown = JSON.parse(call.resultJson ?? "{}");
    const data: unknown = result && typeof result === "object" ? Reflect.get(result, "data") : undefined;
    const structured = data && typeof data === "object" ? Reflect.get(data, "structuredContent") : undefined;
    for (const record of [data, structured]) {
      if (!record || typeof record !== "object") continue;
      for (const key of ["created", "modified", "deleted"]) {
        const values: unknown = Reflect.get(record, key);
        if (Array.isArray(values)) for (const path of values) add(path, key === "deleted");
      }
      const changes: unknown = Reflect.get(record, "changes");
      if (Array.isArray(changes)) for (const change of changes) {
        if (!change || typeof change !== "object") continue;
        const oldPath: unknown = Reflect.get(change, "path");
        const newPath: unknown = Reflect.get(change, "newPath");
        if (typeof newPath === "string" && newPath !== oldPath) add(oldPath, true);
        add(typeof newPath === "string" ? newPath : oldPath, Reflect.get(change, "kind") === "delete");
      }
    }
  } catch { /* Missing or malformed results do not create file references. */ }
  return [...paths.values()];
}

export function toolFilePaths(call: ToolCall): string[] {
  return toolFileChanges(call).map((change) => change.path);
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
