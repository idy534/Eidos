import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type DragEvent } from "react";
import type { InputPrepareRequest, InputPreview, InputReference } from "../../../shared/input-context.js";
import { userFacingError } from "../session-state.js";

interface InputContextValue {
  sessionId: string;
  workspaceRoot: string;
  busy: boolean;
  error: string | undefined;
  add(request: InputPrepareRequest): Promise<void>;
  paths(paths: string[]): Promise<void>;
  paste(): Promise<void>;
  settings(section?: string): void;
  reuse(reference: InputReference): void;
}
const Context = createContext<InputContextValue | null>(null);
export const useInputContext = () => useContext(Context);

export function InputContextProvider({ sessionId, workspaceRoot, ready, onAdd, onSettings, children }: {
  sessionId: string | undefined; workspaceRoot: string | undefined; ready: boolean;
  onAdd(id: string, reference: InputReference): void;
  onSettings(section?: string): void; children: ReactNode;
}) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const addRef = useRef(onAdd);
  addRef.current = onAdd;
  const work = useCallback(async (run: () => Promise<InputReference | null>, clearError = true) => {
    if (!sessionId) return;
    if (!ready) { setErrors((previous) => ({ ...previous, [sessionId]: "草稿尚未恢复，请稍后再添加引用。" })); return; }
    const owner = sessionId;
    setCounts((previous) => ({ ...previous, [owner]: (previous[owner] ?? 0) + 1 }));
    if (clearError) setErrors((previous) => ({ ...previous, [owner]: undefined }));
    try {
      const reference = await run();
      if (reference) addRef.current(owner, reference);
    } catch (error) {
      setErrors((previous) => ({ ...previous, [owner]: userFacingError(error) }));
    } finally {
      setCounts((previous) => ({ ...previous, [owner]: Math.max(0, (previous[owner] ?? 1) - 1) }));
    }
  }, [sessionId, ready]);
  const add = useCallback((request: InputPrepareRequest) => work(() => window.eidosRuntime.prepareInput({ ...request, ...(sessionId && !sessionId.startsWith("draft-") ? { sessionId } : {}) })), [work, sessionId]);
  const paths = useCallback(async (paths: string[]) => {
    if (paths.length > 20) {
      if (sessionId) setErrors((previous) => ({ ...previous, [sessionId]: "一次最多添加 20 个引用。" }));
      return;
    }
    // Each selection is prepared in order; a failure never discards accepted files.
    if (sessionId) setErrors((previous) => ({ ...previous, [sessionId]: undefined }));
    for (const source of paths) await work(() => window.eidosRuntime.prepareInput({ kind: "file", source, ...(sessionId && !sessionId.startsWith("draft-") ? { sessionId } : {}) }), false);
  }, [work, sessionId]);
  useEffect(() => window.eidosRuntime.onInputQuote((text) => {
    void add({ kind: "excerpt", source: `session:${sessionId ?? ""}:selection`, label: "选中内容", text });
  }), [sessionId, add]);
  return <Context.Provider value={sessionId ? {
    sessionId, workspaceRoot: workspaceRoot ?? "", busy: Boolean(counts[sessionId]), error: errors[sessionId], add, paths,
    paste: () => work(() => window.eidosRuntime.pasteInputImage()), settings: onSettings,
    reuse: (reference) => { if (ready) addRef.current(sessionId, reference); },
  } : null}>{children}</Context.Provider>;
}

export function InputDropZone({ children }: { children: ReactNode }) {
  const context = useInputContext();
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);
  function files(event: DragEvent): boolean { return event.dataTransfer.types.includes("Files"); }
  return <div className={`input-drop-zone${dragging ? " is-dragging" : ""}`}
    onDragEnter={(event) => { if (files(event)) { event.preventDefault(); depth.current++; setDragging(true); } }}
    onDragOver={(event) => { if (files(event)) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; } }}
    onDragLeave={(event) => { if (files(event) && --depth.current <= 0) { depth.current = 0; setDragging(false); } }}
    onDrop={(event) => {
      if (!files(event)) return;
      event.preventDefault(); event.stopPropagation(); depth.current = 0; setDragging(false);
      const paths = Array.from(event.dataTransfer.files).map((file) => window.eidosRuntime.inputPathForFile(file)).filter(Boolean);
      if (paths.length) void context?.paths(paths);
    }}>
    {children}
    {dragging && <div className="input-drop-overlay">添加到当前草稿；文件不会自动发送</div>}
  </div>;
}

export function InputReferenceCards({ references, onRemove }: { references: InputReference[]; onRemove?: (id: string) => void }) {
  const context = useInputContext();
  const [firstLine, setFirstLine] = useState(1);
  const [lastLine, setLastLine] = useState(1);
  const [preview, setPreview] = useState<InputPreview>();
  const [error, setError] = useState<string>();
  const dialog = useRef<HTMLDialogElement>(null);
  const request = useRef(0);
  useEffect(() => { if (preview) { setFirstLine(1); setLastLine(1); dialog.current?.showModal(); } }, [preview]);
  return <>
    <div className="input-reference-list" aria-label="输入引用">
      {references.map((reference) => <div className="input-reference" key={reference.id}>
        <button type="button" title={reference.source} onClick={() => {
          const token = ++request.current;
          setError(undefined);
          void window.eidosRuntime.readInput(reference.id).then((value) => { if (token === request.current) setPreview(value); }, (cause) => { if (token === request.current) setError(userFacingError(cause)); });
        }}><span>{reference.kind === "image" ? <InputThumbnail id={reference.id} label={reference.label} /> : reference.kind === "directory" ? "▣" : "↗"}</span> {reference.label}
          <small>{reference.status === "content" ? "内容快照" : reference.status === "selection" ? "扩展选择" : "位置引用"}</small>
        </button>
        {onRemove && <button type="button" className="input-reference-remove" aria-label={`移除 ${reference.label}`} onClick={() => onRemove(reference.id)}>×</button>}
      </div>)}
    </div>
    {error && <p role="alert" className="input-reference-error">{error}</p>}
    {preview && <dialog className="input-reference-preview" ref={dialog} onClose={() => setPreview(undefined)} aria-label={preview.reference.label}>
      <h3>{preview.reference.label}</h3>
      <p className="input-reference-source">{preview.reference.source}</p>
      {preview.thumbnail && <img alt={preview.reference.label} src={preview.thumbnail} />}
      <pre>{preview.text}</pre>
      {context && <button type="button" onClick={() => { context.reuse(preview.reference); dialog.current?.close(); }}>添加到当前输入</button>}
      {context && preview.reference.kind === "file" && preview.reference.status === "content" && <div className="input-reference-lines">
        <label>起始行 <input type="number" min={1} value={firstLine} onChange={(event) => setFirstLine(Number(event.target.value))} /></label>
        <label>结束行 <input type="number" min={firstLine} value={lastLine} onChange={(event) => setLastLine(Number(event.target.value))} /></label>
        <button type="button" disabled={!Number.isSafeInteger(firstLine) || firstLine < 1 || !Number.isSafeInteger(lastLine) || lastLine < firstLine} onClick={() => {
          void context.add({ kind: "file", source: preview.reference.source, startLine: firstLine, endLine: lastLine }); dialog.current?.close();
        }}>引用当前文件的这些行</button>
      </div>}
      <button type="button" autoFocus onClick={() => dialog.current?.close()}>关闭</button>
    </dialog>}
  </>;
}

function InputThumbnail({ id, label }: { id: string; label: string }) {
  const root = useRef<HTMLSpanElement>(null);
  const [source, setSource] = useState<string>();
  useEffect(() => {
    let active = true;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      void window.eidosRuntime.readInput(id).then((value) => {
        if (active && value.thumbnail) setSource(value.thumbnail);
      }, () => {});
    });
    if (root.current) observer.observe(root.current);
    return () => { active = false; observer.disconnect(); };
  }, [id]);
  return <span ref={root} className="input-thumbnail">{source ? <img src={source} alt={label} /> : "▧"}</span>;
}
