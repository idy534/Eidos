import { useCallback, useEffect, useState } from "react";
import type { MemoryScope } from "../../../../shared/memory.generated.js";
import { Button } from "../Button.js";
import { MemoryNoteEditor } from "./MemoryNoteEditor.js";
import { useMemoryDocuments, type MemoryScopeChoice } from "./useMemoryDocuments.js";

const modes = { off: "关闭", read: "只读", auto: "自动" } as const;
type MemoryMode = MemoryScope["mode"];
const descriptions = {
  off: "模型不使用或自动更新这里的记忆。你仍可查看和编辑笔记。",
  read: "模型可以使用已有记忆；用户明确要求时才会保存或修改。",
  auto: "模型在当前对话中及时记下有长期价值的信息，保存后即可使用。",
} as const;

export function MemorySettings({ sessionId, onOpenSource, onShowToast }: {
  sessionId?: string | undefined;
  onOpenSource?: ((id: string) => void) | undefined;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const [scope, setScope] = useState<MemoryScopeChoice>(sessionId ? "current" : "global");
  const [path, setPath] = useState<string | null>("MEMORY.md");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const library = useMemoryDocuments(sessionId, scope);
  const scopes = library.state?.scopes ?? [];
  const selected = scope === "global" ? scopes[0] : scopes.at(-1);
  const setEditorDirty = useCallback((value: boolean) => setDirty(value), []);
  useEffect(() => { setPath("MEMORY.md"); setDirty(false); setError(""); }, [sessionId, scope]);

  async function changeMode(mode: MemoryMode) {
    setBusy(true); setError("");
    try {
      await window.eidosRuntime.memory("memory/policy", { sessionId: sessionId ?? null, scope, mode });
      library.refresh();
    } catch { setError("记忆模式未能保存，请重试。"); }
    finally { setBusy(false); }
  }

  async function exportNotes() {
    setBusy(true); setError("");
    try { await window.eidosRuntime.exportMemoryNotes(scope, sessionId); }
    catch { setError("导出未完成，请重试。"); }
    finally { setBusy(false); }
  }

  return <div className="settings-panel memory-settings">
    <header className="settings-panel-header"><h1>记忆</h1><p className="settings-panel-subtitle">查看 Eidos 记住了什么，并直接修改笔记。</p></header>
    <div className="memory-toolbar">
      {scopes.length > 1 && <label>范围<select aria-label="记忆范围" value={scope} disabled={dirty || busy} onChange={(event) => setScope(event.target.value as MemoryScopeChoice)}>
        <option value="current">当前项目</option><option value="global">全局</option>
      </select></label>}
      <label>记忆模式<select aria-label="记忆模式" value={selected?.mode ?? "auto"} disabled={!selected || busy || dirty || library.state?.temporary} onChange={(event) => void changeMode(event.target.value as MemoryMode)}>
        {Object.entries(modes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <Button size="small" variant="ghost" disabled={!selected || busy || dirty} onClick={() => void exportNotes()}>导出 Markdown</Button>
    </div>
    <p className="memory-policy-description">{library.state?.temporary ? "当前是临时对话，模型不会使用或保存记忆。" : descriptions[selected?.mode ?? "auto"]}</p>
    {(error || library.error) && <div role="alert" className="memory-error">{error || library.error}<Button size="small" variant="ghost" onClick={library.refresh}>重试</Button></div>}
    <div className="memory-workspace">
      <nav className="memory-files" aria-label="记忆文件">
        <div className="memory-files-tools"><input aria-label="搜索记忆" type="search" placeholder="搜索笔记…" maxLength={512} value={library.query} disabled={dirty} onChange={(event) => library.setQuery(event.target.value)} />
          <Button variant="ghost" size="small" disabled={dirty || busy || library.state?.temporary} onClick={() => setPath(null)}>新建笔记</Button></div>
        <button type="button" className="memory-file" aria-pressed={path === "MEMORY.md"} disabled={dirty} onClick={() => setPath("MEMORY.md")}><strong>MEMORY.md</strong><span>记忆索引</span></button>
        {library.loading && !library.state && <p role="status">正在读取记忆…</p>}
        {library.state?.documents.map((note) => <button type="button" key={note.id} className="memory-file" aria-pressed={path === note.path} disabled={dirty} onClick={() => setPath(note.path)}><strong>{note.path}</strong><span>{note.summary}</span></button>)}
        {library.state?.documents.length === 0 && <p className="memory-files-empty">{library.query ? "没有匹配的笔记。" : "这里还没有主题笔记。你可以在对话中说“记住这个偏好”，也可以新建笔记。"}</p>}
        {library.state?.searchLimited && <p className="memory-files-empty">匹配的笔记较多，请细化关键词以查找其他结果。</p>}
        <div className="memory-file-pages">{library.cursor > 0 && <Button variant="ghost" size="small" disabled={dirty} onClick={() => library.setCursor(0)}>返回第一页</Button>}
          {library.state?.nextCursor != null && <Button variant="ghost" size="small" disabled={dirty} onClick={() => library.setCursor(library.state!.nextCursor!)}>下一页</Button>}</div>
      </nav>
      {selected && <MemoryNoteEditor key={`${sessionId}:${selected.id}:${path}`} sessionId={sessionId} scope={scope} path={path} generation={selected.generation}
        readOnly={library.state?.temporary}
        onDirty={setEditorDirty} onSelect={setPath} onOpenSource={onOpenSource}
        onSaved={(saved) => { setPath(saved); library.refresh(); onShowToast?.("记忆已更新", "success"); }} />}
    </div>
  </div>;
}
