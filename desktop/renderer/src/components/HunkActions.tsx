import { Fragment, useEffect, useState } from "react";
import { Diff, Hunk, Decoration, parseDiff, type FileData } from "react-diff-view";
import type { GitReviewPatch } from "../contracts.js";
import { userFacingError } from "../session-state.js";

export function HunkActions({ sessionId, path, layer, disabled, onChanged }: {
  sessionId: string; path: string; layer: "staged" | "unstaged"; disabled: boolean; onChanged(): void;
}) {
  const [opened, setOpened] = useState(false);
  const [patch, setPatch] = useState<GitReviewPatch>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!opened) return;
    let current = true;
    setPatch(undefined); setError("");
    void window.eidosRuntime.readGitReviewPatch(sessionId, path, layer).then((next) => { if (current) setPatch(next); }).catch((cause) => { if (current) setError(userFacingError(cause)); });
    return () => { current = false; };
  }, [opened, sessionId, path, layer, revision]);
  let files: FileData[] = [];
  try { files = parseDiff(patch?.patch ?? ""); } catch { /* Preserve the raw patch and disable actions for unsupported input. */ }
  async function apply(hunkIndex: number, action: "stage" | "unstage" | "discard") {
    if (!patch) return;
    if (action === "discard" && !window.confirm("丢弃这个修改块？此操作无法撤销。")) return;
    setBusy(true); setError("");
    try {
      await window.eidosRuntime.applyGitHunk(sessionId, { path, action, hunkIndex, diffHash: patch.diffHash, operationId: crypto.randomUUID() });
      setRevision((value) => value + 1); onChanged();
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setBusy(false); }
  }
  return <details onToggle={(event) => setOpened(event.currentTarget.open)}><summary>按修改块操作 · {layer === "staged" ? "已暂存" : "未暂存"}</summary>
    {error && <p role="alert">{error}</p>}
    {!patch && !error && opened && <p role="status">正在读取修改块…</p>}
    {files.map((file) => <Diff key={`${file.oldPath}:${file.newPath}`} viewType="unified" diffType={file.type} hunks={file.hunks}>
      {(hunks) => hunks.map((hunk, index) => <Fragment key={hunk.content}><Decoration><div className="artifact-toolbar">
        <button disabled={disabled || busy || file.type !== "modify"} onClick={() => void apply(index, layer === "staged" ? "unstage" : "stage")}>{layer === "staged" ? "取消暂存此块" : "暂存此块"}</button>
        {layer === "unstaged" && <button disabled={disabled || busy || file.type !== "modify"} onClick={() => void apply(index, "discard")}>丢弃此块</button>}
      </div></Decoration><Hunk hunk={hunk} /></Fragment>)}
    </Diff>)}
    {patch && !files.some((file) => file.hunks.length) && <p>当前没有可操作的文本修改块。新增、删除和二进制文件请使用整文件操作。</p>}
    <button type="button" disabled={busy} onClick={() => setRevision((value) => value + 1)}>刷新修改块</button>
  </details>;
}
