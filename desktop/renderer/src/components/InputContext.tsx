import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type DragEvent } from "react";
import { createPortal } from "react-dom";
import type { InputPrepareRequest, InputPreview, InputReference } from "../../../shared/input-context.js";
import { userFacingError } from "../session-state.js";
import { FileIcon, FolderIcon, SparklesIcon, ServerIcon, PuzzleIcon, ChatHistoryIcon } from "./InputPicker.js";
import { SkillDetailDialog } from "./settings/SkillDetailDialog.js";

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
  navigateToSession?: ((sessionId: string) => void) | undefined;
}
const Context = createContext<InputContextValue | null>(null);
export const useInputContext = () => useContext(Context);

export function InputContextProvider({
  sessionId,
  workspaceRoot,
  ready,
  onAdd,
  onSettings,
  onNavigateToSession,
  children,
}: {
  sessionId: string | undefined;
  workspaceRoot: string | undefined;
  ready: boolean;
  onAdd(id: string, reference: InputReference): void;
  onSettings(section?: string): void;
  onNavigateToSession?: ((sessionId: string) => void) | undefined;
  children: ReactNode;
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
  useEffect(() => window.eidosRuntime?.onInputQuote?.((text) => {
    void add({ kind: "excerpt", source: `session:${sessionId ?? ""}:selection`, label: "选中内容", text });
  }), [sessionId, add]);
  return <Context.Provider value={sessionId ? {
    sessionId, workspaceRoot: workspaceRoot ?? "", busy: Boolean(counts[sessionId]), error: errors[sessionId], add, paths,
    paste: () => work(() => window.eidosRuntime.pasteInputImage()), settings: onSettings,
    reuse: (reference) => { if (ready) addRef.current(sessionId, reference); },
    navigateToSession: onNavigateToSession,
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
  const [preview, setPreview] = useState<InputPreview>();
  const [error, setError] = useState<string>();
  const request = useRef(0);

  return (
    <>
      <div className="input-reference-list" aria-label="输入引用">
        {references.map((reference) => (
          <div className="input-reference" key={reference.id}>
            <button
              type="button"
              className="input-reference__btn"
              title={
                reference.kind === "history"
                  ? "跳转到该对话"
                  : reference.kind === "file" || reference.kind === "directory"
                    ? `在 Finder 中显示：${reference.source}`
                    : reference.source
              }
              onClick={async () => {
                if (reference.kind === "history") {
                  context?.navigateToSession?.(reference.source);
                  return;
                }
                if (reference.kind === "file" || reference.kind === "directory") {
                  try {
                    await window.eidosRuntime.showItemInFolder(reference.source);
                  } catch (cause) {
                    setError(userFacingError(cause));
                  }
                  return;
                }
                const token = ++request.current;
                setError(undefined);
                void window.eidosRuntime.readInput(reference.id).then((value) => {
                  if (token === request.current) setPreview(value);
                }, (cause) => {
                  if (token === request.current) setError(userFacingError(cause));
                });
              }}
            >
              <span className={`input-reference__icon input-reference__icon--${reference.kind}`}>
                {renderReferenceIcon(reference)}
              </span>
              <span className="input-reference__label">{reference.label}</span>
            </button>
            {onRemove && (
              <button
                type="button"
                className="input-reference-remove"
                aria-label={`移除 ${reference.label}`}
                title={`移除 ${reference.label}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onRemove(reference.id);
                }}
              >
                <CloseIcon />
              </button>
            )}
          </div>
        ))}
      </div>
      {error && <p role="alert" className="input-reference-error">{error}</p>}
      {preview && (
        preview.reference.kind === "image" ? (
          <ImageLightboxModal
            preview={preview}
            onClose={() => setPreview(undefined)}
          />
        ) : preview.reference.kind === "skill" ? (
          <SkillDetailDialog
            skill={{
              qualifiedId: preview.reference.source,
              name: preview.reference.label,
              description: preview.text,
              sourceKind: "user",
              enabled: true,
              available: true,
              schemaVersion: 1,
              pluginId: "",
              pluginVersion: "",
              pluginHash: "",
              contentHash: preview.reference.sha256 || "",
            }}
            readOnly
            onClose={() => setPreview(undefined)}
          />
        ) : null
      )}
    </>
  );
}

function ImageLightboxModal({
  preview,
  onClose,
}: {
  preview: InputPreview;
  onClose(): void;
}) {
  const [url, setUrl] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    let active = true;
    let created: string | undefined;
    if (typeof window.eidosRuntime?.prepareInputPreview !== "function") return;
    void window.eidosRuntime.prepareInputPreview(preview.reference.id).then((previewUrl) => {
      created = previewUrl;
      if (active) setUrl(previewUrl);
      else if (typeof window.eidosRuntime?.releaseInputPreview === "function") {
        void window.eidosRuntime.releaseInputPreview(previewUrl).catch(() => {});
      }
    }).catch((err) => {
      if (active) setError(err instanceof Error ? err.message : "无法加载原图预览。");
    });
    return () => {
      active = false;
      if (created && typeof window.eidosRuntime?.releaseInputPreview === "function") {
        void window.eidosRuntime.releaseInputPreview(created).catch(() => {});
      }
    };
  }, [preview.reference.id]);

  if (typeof document === "undefined") return null;

  const displaySrc = url || preview.thumbnail;

  return createPortal(
    <div
      className="artifact-image-preview"
      role="dialog"
      aria-modal="true"
      aria-label={`${preview.reference.label} 图片预览`}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <button
        type="button"
        className="artifact-image-preview__close"
        aria-label="关闭图片预览"
        autoFocus
        onClick={onClose}
      >
        ×
      </button>
      {displaySrc ? (
        <img src={displaySrc} alt={preview.reference.label} />
      ) : (
        <p role={error ? "alert" : "status"}>{error || "正在读取图片预览…"}</p>
      )}
    </div>,
    document.body,
  );
}

function renderReferenceIcon(reference: InputReference) {
  switch (reference.kind) {
    case "file":
    case "excerpt":
      return <FileIcon />;
    case "directory":
      return <FolderIcon />;
    case "skill":
      return <SparklesIcon />;
    case "mcp":
      return <ServerIcon />;
    case "plugin":
      return <PuzzleIcon />;
    case "history":
      return <ChatHistoryIcon />;
    case "image":
      return <InputThumbnail id={reference.id} label={reference.label} />;
    default:
      return <FileIcon />;
  }
}

function ImageIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <rect x="2.5" y="2.5" width="11" height="11" rx="2" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="5.5" cy="5.5" r="1" fill="currentColor" />
      <path d="m3 12 4-4 2.5 2.5 2-2 2 2" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="10" height="10" fill="none" aria-hidden="true">
      <path d="m4 4 8 8m0-8-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
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
  return <span ref={root} className="input-thumbnail">{source ? <img src={source} alt={label} /> : <ImageIcon />}</span>;
}

