import { useEffect, useState } from "react";
import type { ToolTextPage } from "../../../shared/domain-contracts.js";
import { userFacingError } from "../session-state.js";

export function ToolTextView({ sessionId, toolCallId, field, sha256, totalBytes }: {
  sessionId: string; toolCallId: string; field: "diff" | "result"; sha256: string; totalBytes: number;
}) {
  const [offsets, setOffsets] = useState<number[]>([]);
  const [page, setPage] = useState<ToolTextPage>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const offset = offsets.at(-1);
  useEffect(() => {
    if (offset === undefined) return;
    let active = true;
    setLoading(true); setError(""); setPage(undefined);
    void window.eidosRuntime.readToolText(sessionId, toolCallId, field, sha256, offset)
      .then((value) => { if (active) setPage(value); })
      .catch((cause: unknown) => { if (active) setError(userFacingError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sessionId, toolCallId, field, sha256, offset]);
  return <section aria-label={field === "diff" ? "完整补丁" : "完整工具结果"}>
    <p>完整{field === "diff" ? "补丁" : "结果"}共 {totalBytes.toLocaleString()} 字节。</p>
    {offset === undefined
      ? <button type="button" onClick={() => setOffsets([0])}>查看完整{field === "diff" ? "补丁" : "结果"}</button>
      : <>
        <button type="button" disabled={loading || offsets.length < 2} onClick={() => setOffsets((values) => values.slice(0, -1))}>上一页</button>
        <button type="button" disabled={loading || !page || page.nextOffset >= page.totalCharacters} onClick={() => { if (page) setOffsets((values) => [...values, page.nextOffset]); }}>下一页</button>
        {page && <><p>字符 {offset + 1}–{page.nextOffset} / {page.totalCharacters}</p><pre className="diff-view">{page.content}</pre></>}
      </>}
    {loading && <p role="status">正在读取…</p>}
    {error && <p role="alert">{error} <button type="button" onClick={() => setOffsets([])}>重新读取</button></p>}
  </section>;
}
