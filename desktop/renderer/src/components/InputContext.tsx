import { createContext, Fragment, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode, type DragEvent } from "react";
import { createPortal } from "react-dom";
import type { InputPrepareRequest, InputPreview, InputReference } from "../../../shared/input-context.js";
import { isInlineReference, isShelfReference, parseInlineSegments } from "../../../shared/inline-references.js";
import type { McpServerRecord } from "../contracts.js";
import { userFacingError } from "../session-state.js";
import { CloseIcon as ImageCloseIcon } from "./MenuIcons.js";
import { ToastContainer } from "./ToastContainer.js";
import { FileIcon, FolderIcon, AgentIcon, SkillIcon, ServerIcon, PuzzleIcon, ChatHistoryIcon } from "./InputPicker.js";
import { AgentDefinitionDialog } from "./AgentDefinitionDialog.js";
import { McpDetailDialog } from "./settings/McpDetailDialog.js";
import { SkillDetailDialog } from "./settings/SkillDetailDialog.js";

export { isInlineReference, isShelfReference };

export function useInlineTokenMetrics(
  _editorRef: { current: HTMLElement | null },
  _inlineReferences: readonly InputReference[],
): void {}

export interface InputEditorSelectionBridge {
  getSelectionRange(): { start: number; end: number } | undefined;
  onInsertedCaret(offset: number): void;
}

interface InputContextValue {
  sessionId: string;
  workspaceRoot: string;
  workspaceRoots: readonly string[];
  projectId: string | undefined;
  busy: boolean;
  error: string | undefined;
  clearError?: () => void;
  add(request: InputPrepareRequest, range?: { start: number; end: number }): Promise<void>;
  paths(paths: string[]): Promise<void>;
  paste(): Promise<void>;
  settings(section?: string): void;
  reuse(reference: InputReference): void;
  navigateToSession?: ((sessionId: string) => void) | undefined;
  registerEditor?: ((bridge: InputEditorSelectionBridge) => () => void) | undefined;
}
const Context = createContext<InputContextValue | null>(null);
export const useInputContext = () => useContext(Context);

export function InputContextProvider({
  sessionId,
  workspaceRoot,
  workspaceRoots,
  projectId,
  ready,
  onAdd,
  onInsert,
  onSettings,
  onNavigateToSession,
  children,
}: {
  sessionId: string | undefined;
  workspaceRoot: string | undefined;
  workspaceRoots?: readonly string[] | undefined;
  projectId?: string | undefined;
  ready: boolean;
  onAdd(id: string, reference: InputReference): void;
  onInsert?: ((id: string, reference: InputReference, range?: { start: number; end: number }) => number | undefined) | undefined;
  onSettings(section?: string): void;
  onNavigateToSession?: ((sessionId: string) => void) | undefined;
  children: ReactNode;
}) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const addRef = useRef(onAdd);
  addRef.current = onAdd;
  const insertRef = useRef(onInsert);
  insertRef.current = onInsert;
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
  const editorBridgeRef = useRef<InputEditorSelectionBridge | null>(null);

  const commitReference = useCallback((
    owner: string,
    reference: InputReference,
    explicitRange?: { start: number; end: number },
  ) => {
    if (insertRef.current && isInlineReference(reference)) {
      const range =
        explicitRange
        ?? (owner === sessionIdRef.current ? editorBridgeRef.current?.getSelectionRange() : undefined);
      const caretOffset = insertRef.current(owner, reference, range);
      if (caretOffset !== undefined && owner === sessionIdRef.current) {
        editorBridgeRef.current?.onInsertedCaret(caretOffset);
      }
      return;
    }
    addRef.current(owner, reference);
  }, []);

  const work = useCallback(async (
    run: () => Promise<InputReference | null>,
    clearError = true,
    explicitRange?: { start: number; end: number },
  ) => {
    if (!sessionId) return;
    if (!ready) { setErrors((previous) => ({ ...previous, [sessionId]: "草稿尚未恢复，请稍后再添加引用。" })); return; }
    const owner = sessionId;
    setCounts((previous) => ({ ...previous, [owner]: (previous[owner] ?? 0) + 1 }));
    if (clearError) setErrors((previous) => ({ ...previous, [owner]: undefined }));
    try {
      const reference = await run();
      if (reference) commitReference(owner, reference, explicitRange);
    } catch (error) {
      setErrors((previous) => ({ ...previous, [owner]: userFacingError(error) }));
    } finally {
      setCounts((previous) => ({ ...previous, [owner]: Math.max(0, (previous[owner] ?? 1) - 1) }));
    }
  }, [sessionId, ready, commitReference]);

  const add = useCallback((request: InputPrepareRequest, range?: { start: number; end: number }) => work(
    () => window.eidosRuntime.prepareInput({ ...request, ...(sessionId && !sessionId.startsWith("draft-") ? { sessionId } : {}) }),
    true,
    range,
  ), [work, sessionId]);

  const paths = useCallback(async (paths: string[]) => {
    if (paths.length > 20) {
      if (sessionId) setErrors((previous) => ({ ...previous, [sessionId]: "一次最多添加 20 个引用。" }));
      return;
    }
    // Each selection is prepared in order; a failure never discards accepted files.
    if (sessionId) setErrors((previous) => ({ ...previous, [sessionId]: undefined }));
    for (const source of paths) await work(() => window.eidosRuntime.prepareInput({ kind: "file", source, ...(sessionId && !sessionId.startsWith("draft-") ? { sessionId } : {}) }), false);
  }, [work, sessionId]);

  const registerEditor = useCallback((bridge: InputEditorSelectionBridge) => {
    editorBridgeRef.current = bridge;
    return () => {
      if (editorBridgeRef.current === bridge) {
        editorBridgeRef.current = null;
      }
    };
  }, []);

  useEffect(() => window.eidosRuntime?.onInputQuote?.((text) => {
    void add({ kind: "excerpt", source: `session:${sessionId ?? ""}:selection`, label: "选中内容", text });
  }), [sessionId, add]);

  const clearError = useCallback(() => {
    if (sessionId) {
      setErrors((previous) => ({ ...previous, [sessionId]: undefined }));
    }
  }, [sessionId]);

  return <Context.Provider value={sessionId ? {
    sessionId, workspaceRoot: workspaceRoot ?? "", workspaceRoots: workspaceRoots ?? (workspaceRoot ? [workspaceRoot] : []), projectId, busy: Boolean(counts[sessionId]), error: errors[sessionId], clearError, add, paths,
    paste: () => work(() => window.eidosRuntime.pasteInputImage()), settings: onSettings,
    reuse: (reference) => { if (ready) commitReference(sessionId, reference); },
    navigateToSession: onNavigateToSession,
    registerEditor,
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

export function getReferenceButtonTitle(reference: InputReference): string {
  switch (reference.kind) {
    case "history":
      return "跳转到该对话";
    case "file":
    case "directory":
      return `在 Finder 中显示：${reference.source}`;
    case "skill":
      return `查看技能详情：${reference.label}`;
    case "agent":
      return `查看智能体详情：${reference.label}`;
    case "mcp":
      return `查看 MCP 详情：${reference.label}`;
    case "plugin":
      return `查看插件详情：${reference.label}`;
    case "excerpt":
      return `查看引用文本：${reference.label}`;
    default:
      return reference.source;
  }
}

export function useReferenceActivation() {
  const context = useInputContext();
  const [preview, setPreview] = useState<InputPreview>();
  const [mcpServer, setMcpServer] = useState<McpServerRecord | null>(null);
  const [error, setError] = useState<string>();
  const request = useRef(0);

  const activateReference = useCallback(async (reference: InputReference) => {
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
    if (
      reference.kind === "mcp"
      && typeof window.eidosRuntime?.listMcpServers === "function"
    ) {
      try {
        const result = await window.eidosRuntime.listMcpServers();
        if (token !== request.current) return;
        const matched = result.servers.find(
          (server) => server.serverId === reference.source || server.serverId === reference.label,
        );
        if (matched) {
          setMcpServer(matched);
          return;
        }
      } catch {
        // Fall back to snapshot preview below
      }
    }
    void window.eidosRuntime.readInput(reference.id).then((value) => {
      if (token === request.current) setPreview(value);
    }, (cause) => {
      if (token === request.current) setError(userFacingError(cause));
    });
  }, [context]);

  const dialogs = (
    <>
      {mcpServer && (
        <McpDetailDialog
          server={mcpServer}
          icon={<ServerIcon />}
          onClose={() => setMcpServer(null)}
        />
      )}
      {preview && (
        preview.reference.kind === "image" ? (
          <ImageLightboxModal
            preview={preview}
            onClose={() => setPreview(undefined)}
          />
        ) : preview.reference.kind === "agent" ? (
          <AgentDefinitionDialog preview={preview} onClose={() => setPreview(undefined)} />
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
        ) : (
          <ReferenceTextPreviewDialog
            preview={preview}
            onClose={() => setPreview(undefined)}
          />
        )
      )}
    </>
  );

  return { activateReference, error, clearError: () => setError(undefined), dialogs };
}

export function InlineReferenceProse({
  content,
  references,
  variant = "bubble",
}: {
  content: string;
  references: readonly InputReference[];
  variant?: "composer" | "bubble";
}) {
  const { activateReference, error, clearError, dialogs } = useReferenceActivation();
  const inlineRefs = references.filter(isInlineReference);
  const segments = parseInlineSegments(content, inlineRefs);
  if (segments.length === 0) return null;

  return (
    <>
      <p className="eidos-chat-prose eidos-inline-reference-prose">
        {segments.map((segment, index) => {
          if (segment.type === "text") {
            return <Fragment key={`text-${index}`}>{segment.text}</Fragment>;
          }
          const reference = segment.reference;
          const cat = getReferenceCategory(reference);
          return (
            <span
              key={`ref-${reference.id}-${index}`}
              className={`input-reference input-reference--${variant} input-reference--kind-${reference.kind} input-reference--inline-token`}
            >
              <button
                type="button"
                className="input-reference__btn"
                title={getReferenceButtonTitle(reference)}
                onClick={() => void activateReference(reference)}
              >
                <span
                  className={`input-reference__icon input-reference__icon--${reference.kind} input-reference__icon--cat-${cat}`}
                >
                  {renderReferenceIcon(reference, cat)}
                </span>
                <span className="input-reference__label">{reference.label}</span>
              </button>
            </span>
          );
        })}
      </p>
      {error && (
        <ToastContainer
          toasts={[{
            id: `input-reference-list-error-${error}`,
            message: error,
            type: "error",
            duration: 3500,
          }]}
          onDismiss={() => clearError()}
        />
      )}
      {dialogs}
    </>
  );
}

export function InputReferenceCards({
  references,
  onRemove,
  variant = "composer",
  placement = "all",
}: {
  references: InputReference[];
  onRemove?: (id: string) => void;
  variant?: "composer" | "bubble";
  placement?: "all" | "shelf" | "inline";
}) {
  const { activateReference, error, clearError, dialogs } = useReferenceActivation();

  const visibleReferences =
    placement === "shelf"
      ? references.filter(isShelfReference)
      : placement === "inline"
        ? references.filter(isInlineReference)
        : references;

  if (visibleReferences.length === 0) return null;

  return (
    <>
      <div
        className={`input-reference-list input-reference-list--${variant} input-reference-list--${placement}`}
        data-variant={variant}
        data-placement={placement}
        aria-label={placement === "inline" ? "内联输入引用" : "输入引用"}
      >
        {visibleReferences.map((reference) => {
          const cat = getReferenceCategory(reference);
          const shelf = isShelfReference(reference);
          const zoneClass = shelf ? "input-reference--shelf-card" : "input-reference--inline-token";
          return (
            <div
              className={`input-reference input-reference--${variant} input-reference--kind-${reference.kind} ${zoneClass}`}
              key={reference.id}
            >
              <button
                type="button"
                className="input-reference__btn"
                title={getReferenceButtonTitle(reference)}
                onKeyDown={(event) => {
                  if (
                    !shelf
                    && onRemove
                    && (event.key === "Backspace" || event.key === "Delete")
                  ) {
                    event.preventDefault();
                    onRemove(reference.id);
                  }
                }}
                onClick={() => void activateReference(reference)}
              >
                {reference.kind === "image" ? (
                  <>
                    <span
                      className={`input-reference__icon input-reference__icon--${reference.kind} input-reference__icon--cat-${cat}`}
                    >
                      {renderReferenceIcon(reference, cat)}
                    </span>
                    <span className="input-reference__label sr-only">{reference.label}</span>
                  </>
                ) : reference.kind === "file" || reference.kind === "excerpt" ? (
                  <>
                    <InputTextSnippet
                      id={reference.id}
                      status={reference.status}
                      category={cat}
                      label={reference.label}
                    />
                    <span className="input-reference__card-footer">
                      <span
                        className={`input-reference__icon input-reference__icon--${reference.kind} input-reference__icon--cat-${cat}`}
                      >
                        {renderReferenceIcon(reference, cat)}
                      </span>
                      <span className="input-reference__label">{reference.label}</span>
                    </span>
                  </>
                ) : reference.kind === "directory" ? (
                  <>
                    <span
                      className={`input-reference__icon input-reference__icon--${reference.kind} input-reference__icon--cat-${cat}`}
                    >
                      {renderReferenceIcon(reference, cat)}
                    </span>
                    <span className="input-reference__dir-meta">
                      <span className="input-reference__label">{reference.label}</span>
                      <span className="input-reference__sublabel">文件夹</span>
                    </span>
                  </>
                ) : (
                  <>
                    <span
                      className={`input-reference__icon input-reference__icon--${reference.kind} input-reference__icon--cat-${cat}`}
                    >
                      {renderReferenceIcon(reference, cat)}
                    </span>
                    <span className="input-reference__label">{reference.label}</span>
                  </>
                )}
              </button>
              {onRemove && shelf && (
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
          );
        })}
      </div>
      {error && (
        <ToastContainer
          toasts={[{
            id: `input-reference-list-error-${error}`,
            message: error,
            type: "error",
            duration: 3500,
          }]}
          onDismiss={() => clearError()}
        />
      )}
      {dialogs}
    </>
  );
}

function ReferenceTextPreviewDialog({
  preview,
  onClose,
}: {
  preview: InputPreview;
  onClose(): void;
}) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const trigger = document.activeElement;
    closeRef.current?.focus();
    return () => {
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);
  const kindLabel =
    preview.reference.kind === "mcp"
      ? "MCP"
      : preview.reference.kind === "plugin"
        ? "插件"
        : preview.reference.kind === "excerpt"
          ? "引用文本"
          : "引用详情";
  const closeLabel = `关闭${kindLabel}详情`;
  return createPortal(
    <div className="modal-backdrop skill-detail-backdrop" onClick={onClose}>
      <div
        className="skill-detail-dialog agent-definition-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
          }
          if (event.key === "Tab") {
            event.preventDefault();
            (document.activeElement === closeRef.current ? bodyRef.current : closeRef.current)?.focus();
          }
        }}
      >
        <div className="skill-detail-header">
          <div className="skill-detail-header-left">
            <span className="skill-icon agent-icon" aria-hidden="true">
              {preview.reference.kind === "mcp" ? (
                <ServerIcon />
              ) : preview.reference.kind === "plugin" ? (
                <PuzzleIcon />
              ) : (
                <ExcerptIcon />
              )}
            </span>
            <div className="skill-detail-title-group">
              <h2 id={titleId}>{preview.reference.label}</h2>
              <span className="skill-type-label">{kindLabel}</span>
            </div>
          </div>
          <div className="skill-detail-header-right">
            <div className="skill-detail-actions">
              <button
                ref={closeRef}
                type="button"
                className="skill-detail-close-btn"
                aria-label={closeLabel}
                onClick={onClose}
              >
                ×
              </button>
            </div>
          </div>
        </div>
        <div className="skill-detail-body">
          <pre
            ref={bodyRef}
            tabIndex={0}
            className="agent-definition-content"
            aria-label={`${kindLabel}内容`}
          >
            {preview.text}
          </pre>
        </div>
      </div>
    </div>,
    document.body,
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
        <ImageCloseIcon />
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

function getReferenceCategory(reference: InputReference): string {
  if (reference.kind !== "file") return reference.kind;
  const label = reference.label.toLowerCase();
  const dotIndex = label.lastIndexOf(".");
  if (dotIndex === -1) return "file";
  const ext = label.slice(dotIndex + 1);
  if (ext === "pdf") return "pdf";
  if (ext === "doc" || ext === "docx") return "doc";
  if (["ts", "tsx", "js", "jsx", "py", "rs", "go", "java", "c", "cpp", "h", "hpp", "html", "css", "scss", "json", "yaml", "yml", "sql", "sh", "toml"].includes(ext)) return "code";
  if (["csv", "tsv", "xlsx", "xls", "numbers"].includes(ext)) return "sheet";
  if (["md", "markdown", "txt", "rtf"].includes(ext)) return "text";
  if (["zip", "tar", "gz", "tgz", "7z", "rar"].includes(ext)) return "archive";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "image";
  return "file";
}

function renderReferenceIcon(reference: InputReference, cat: string) {
  switch (reference.kind) {
    case "file": {
      if (cat === "code") return <CodeFileIcon />;
      if (cat === "sheet") return <SheetFileIcon />;
      if (cat === "doc" || cat === "text" || cat === "pdf") return <DocFileIcon />;
      return <FileIcon />;
    }
    case "excerpt":
      return <ExcerptIcon />;
    case "directory":
      return <FolderIcon />;
    case "agent":
      return <AgentIcon />;
    case "skill":
      return <SkillIcon />;
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

function CodeFileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="M3.5 2.5h5.5l3.5 3.5v7.5a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9 2.5V6h3.5" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="m6 9.5-1.25 1.5L6 12.5m4-3 1.25 1.5L10 12.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DocFileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="M3.5 2.5h5.5l3.5 3.5v7.5a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9 2.5V6h3.5" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M5.5 9h5M5.5 11.5h3.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function SheetFileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="M3.5 2.5h5.5l3.5 3.5v7.5a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9 2.5V6h3.5" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M5.5 9h5M5.5 11.5h5M7.5 7.5v5" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ExcerptIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <rect x="2.5" y="2.5" width="11" height="11" rx="2" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.5 6.5h5M5.5 9.5h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
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
    <svg viewBox="0 0 16 16" width="9" height="9" fill="none" aria-hidden="true">
      <path d="m4 4 8 8m0-8-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function InputThumbnail({ id, label }: { id: string; label: string }) {
  const root = useRef<HTMLSpanElement>(null);
  const [source, setSource] = useState<string>();
  useEffect(() => {
    if (typeof window.eidosRuntime?.readInput !== "function") return;
    let active = true;
    const load = () => {
      void window.eidosRuntime.readInput(id).then((value) => {
        if (active && value.thumbnail) setSource(value.thumbnail);
      }, () => {});
    };
    if (typeof IntersectionObserver === "undefined") {
      load();
      return () => { active = false; };
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      load();
    });
    if (root.current) observer.observe(root.current);
    return () => { active = false; observer.disconnect(); };
  }, [id]);
  return <span ref={root} className="input-thumbnail">{source ? <img src={source} alt={label} /> : <ImageIcon />}</span>;
}

function InputTextSnippet({
  id,
  status,
  category,
  label,
}: {
  id: string;
  status: InputReference["status"];
  category: string;
  label: string;
}) {
  const root = useRef<HTMLSpanElement>(null);
  const [snippet, setSnippet] = useState<string>();
  useEffect(() => {
    if ((status !== "content" && status !== "selection") || typeof window.eidosRuntime?.readInput !== "function") return;
    let active = true;
    const load = () => {
      void window.eidosRuntime.readInput(id).then((value) => {
        if (active && value.text) {
          setSnippet(value.text.slice(0, 320));
        }
      }, () => {});
    };
    if (typeof IntersectionObserver === "undefined") {
      load();
      return () => { active = false; };
    }
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      load();
    });
    if (root.current) observer.observe(root.current);
    return () => { active = false; observer.disconnect(); };
  }, [id, status]);

  const dotIndex = label.lastIndexOf(".");
  const ext = dotIndex > 0 && dotIndex < label.length - 1 ? label.slice(dotIndex + 1).toUpperCase().slice(0, 6) : category.toUpperCase();

  return (
    <span
      ref={root}
      className={`input-reference__mini-preview${snippet ? "" : " input-reference__mini-preview--placeholder"}`}
      aria-hidden="true"
    >
      {snippet ? (
        <span className="input-reference__mini-doc">{snippet}</span>
      ) : status === "location" ? (
        <span className="input-reference__mini-badge">{ext}</span>
      ) : (
        <span className="input-reference__mini-lines">
          <span />
          <span />
          <span />
          <span />
        </span>
      )}
    </span>
  );
}

