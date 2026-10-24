import { useEffect, useRef, useState } from "react";
import type { MemoryReadResult } from "../../../../shared/memory.generated.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { MarkdownContent } from "../MarkdownContent.js";
import type { MemoryScopeChoice } from "./useMemoryDocuments.js";

export function MemoryNoteEditor({ sessionId, scope, path, generation, readOnly = false, onSaved, onSelect, onDirty, onOpenSource }: {
  sessionId: string | undefined; scope: MemoryScopeChoice; path: string | null; generation: number;
  readOnly?: boolean | undefined;
  onSaved: (path: string) => void; onSelect: (path: string) => void; onDirty: (dirty: boolean) => void;
  onOpenSource?: ((id: string) => void) | undefined;
}) {
  const [read, setRead] = useState<MemoryReadResult>();
  const [draft, setDraft] = useState({ path: "", summary: "", content: "" });
  const [editing, setEditing] = useState(path === null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(path !== null);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [revision, setRevision] = useState<number | undefined>();
  const [reload, setReload] = useState(0);
  const mounted = useRef(true);
  const dirty = editing && (draft.content !== (read?.content ?? "")
    || draft.summary !== (read?.document?.summary ?? "") || (path === null && draft.path !== ""));
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  useEffect(() => { onDirty(dirty || busy); }, [dirty, busy, onDirty]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => {
    if (path === null || dirtyRef.current) return;
    let active = true;
    setLoading(true); setError(""); setRead(undefined);
    async function load() {
      let result: MemoryReadResult | undefined;
      let content = "";
      let offset = 0;
      for (let page = 0; page < 10; page++) {
        const value = await window.eidosRuntime.memory("memory/read", {
          sessionId: sessionId ?? null, scope, path: path!, offset, limit: 16384,
          revision: result?.revision ?? revision ?? null,
        });
        result ??= value; content += value.content;
        if (value.nextOffset == null) return { ...result, content };
        if (value.nextOffset <= offset) throw new Error("记忆分页无效。");
        offset = value.nextOffset;
      }
      throw new Error("笔记过长，请通过分页工具读取。");
    }
    void load().then((value) => {
      if (!active) return;
      setRead(value); setDraft({ path: value.path, summary: value.document?.summary ?? "", content: value.content });
    }).catch(() => { if (active) setError("笔记无法读取，可能已更新或删除。请重新读取。"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sessionId, scope, path, generation, revision, reload]);

  async function save(action: "write" | "delete") {
    if (busy || readOnly) return;
    setBusy(true); setError("");
    try {
      await window.eidosRuntime.memory("memory/edit", {
        sessionId: sessionId ?? null, scope, operationId: crypto.randomUUID(), action,
        path: path ?? draft.path, expectedRevision: read?.revision ?? 0,
        ...(action === "write" ? { summary: draft.summary, content: draft.content,
          title: read?.document?.title ?? draft.path.replace(/\.md$/, ""), kind: read?.document?.kind ?? "feedback" } : {}),
      });
      if (!mounted.current) return;
      setEditing(false); setDeleting(false); dirtyRef.current = false;
      onDirty(false); onSaved(action === "delete" ? "MEMORY.md" : path ?? draft.path);
      setReload((value) => value + 1);
    } catch {
      if (mounted.current) setError("保存失败，笔记可能已被其他任务修改。你的编辑仍在此处，请重新核对后保存。");
    } finally { if (mounted.current) setBusy(false); }
  }

  const index = path === "MEMORY.md";
  const current = !read || read.revision === read.currentRevision;
  return <section className="memory-note" aria-label="记忆正文" aria-busy={loading || busy}>
    <header className="memory-note-heading">
      <div><h2>{path ?? "新建笔记"}</h2><p>{index ? "模型先读取这份索引，再按需打开笔记。" : "笔记会保留适用条件，并在后续对话中使用。"}</p></div>
      {!index && !editing && read && current && <Button size="small" variant="secondary" disabled={loading || readOnly} onClick={() => setEditing(true)}>编辑</Button>}
    </header>
    {error && <div role="alert" className="memory-error">{error}<Button size="small" variant="ghost" disabled={busy} onClick={() => { dirtyRef.current = false; setEditing(false); setReload((value) => value + 1); }}>重新读取</Button></div>}
    {loading ? <p role="status">正在读取笔记…</p> : editing ? <form onSubmit={(event) => { event.preventDefault(); void save("write"); }}>
      {path === null && <label>文件名<input aria-label="笔记文件名" placeholder="feedback_testing.md" maxLength={200} value={draft.path} disabled={busy} onChange={(event) => setDraft({ ...draft, path: event.target.value })} /></label>}
      <label>简短摘要<input aria-label="笔记摘要" maxLength={240} value={draft.summary} disabled={busy} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} /></label>
      <label>Markdown 正文<textarea aria-label="笔记正文" rows={16} maxLength={65536} value={draft.content} disabled={busy} onChange={(event) => setDraft({ ...draft, content: event.target.value })} /></label>
      <div className="memory-note-actions">
        <span>{dirty ? "尚未保存" : ""}</span>
        <Button type="button" variant="ghost" disabled={busy} onClick={() => { dirtyRef.current = false; setEditing(false); onDirty(false); if (path === null) onSelect("MEMORY.md"); else setReload((value) => value + 1); }}>取消编辑</Button>
        <Button type="submit" variant="primary" disabled={busy || readOnly || !draft.content.trim() || !draft.summary.trim() || (path === null && !draft.path.endsWith(".md"))}>{busy ? "正在保存…" : "保存笔记"}</Button>
      </div>
    </form> : <div className="memory-note-body"><MarkdownContent content={read?.content ?? ""} onNoteLink={onSelect} /></div>}
    {read?.document && !editing && <details className="memory-note-details">
      <summary>来源和版本</summary>
      <p>更新时间：{new Date(read.document.updatedAt).toLocaleString()}</p>
      <label>版本<select aria-label="记忆版本" value={read.revision} onChange={(event) => setRevision(Number(event.target.value))}>
        {(read.revisions ?? []).map((value) => <option key={value} value={value}>v{value}{value === read.currentRevision ? "（当前）" : ""}</option>)}
      </select></label>
      {(read.document.evidence ?? []).map((source) => <Button key={source.itemId} variant="ghost" size="small" disabled={!onOpenSource} onClick={() => onOpenSource?.(source.sessionId)}>查看来源对话</Button>)}
      {current && <Button variant="danger" size="small" disabled={readOnly} onClick={() => setDeleting(true)}>删除笔记</Button>}
    </details>}
    <ConfirmDialog open={deleting} title="删除这份记忆" description="正文和历史版本将被删除，相关上下文将被撤销。原始聊天会保留。" confirmLabel="删除笔记" isDestructive busy={busy} onConfirm={() => void save("delete")} onCancel={() => setDeleting(false)} />
  </section>;
}
