import { useCallback, useEffect, useRef, useState } from "react";
import type { MemoryScope } from "../../../../shared/memory.generated.js";
import { Button } from "../Button.js";
import { SearchInput } from "../SearchInput.js";
import { StatusBadge } from "../StatusBadge.js";
import { ToastContainer, type ToastItem, type ToastType } from "../ToastContainer.js";
import { MemoryNoteEditor } from "./MemoryNoteEditor.js";
import {
  ExportIcon,
  IndexBookmarkIcon,
  DocumentTextIcon,
  InfoCircleIcon,
  PlusIcon,
  SearchEmptyIcon,
  NotesEmptyIcon,
} from "./MemoryIcons.js";
import { useMemoryDocuments, type MemoryScopeChoice } from "./useMemoryDocuments.js";

const modes = { off: "关闭", read: "只读", auto: "自动" } as const;
type MemoryMode = MemoryScope["mode"];

const modeBadges: Record<MemoryMode, { tone: "neutral" | "info" | "success"; text: string }> = {
  off: { tone: "neutral", text: "已停用" },
  read: { tone: "info", text: "只读生效" },
  auto: { tone: "success", text: "自动记录" },
};

const descriptions = {
  off: "模型不使用或自动更新这里的记忆。你仍可查看和编辑笔记。",
  read: "模型可以使用已有记忆；用户明确要求时才会保存或修改。",
  auto: "模型在当前对话中及时记下有长期价值的信息，保存后即可使用。",
} as const;

export function MemorySettings({
  sessionId,
  onOpenSource,
  onShowToast,
}: {
  sessionId?: string | undefined;
  onOpenSource?: ((id: string) => void) | undefined;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const [scope, setScope] = useState<MemoryScopeChoice>(sessionId ? "current" : "global");
  const [path, setPath] = useState<string | null>("MEMORY.md");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localToasts, setLocalToasts] = useState<ToastItem[]>([]);

  const library = useMemoryDocuments(sessionId, scope);
  const scopes = library.state?.scopes ?? [];
  const selected = scope === "global" ? scopes[0] : scopes.at(-1);
  const setEditorDirty = useCallback((value: boolean) => setDirty(value), []);

  const dismissToast = useCallback((id: string) => {
    setLocalToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const handleShowToast = useCallback(
    (message: string, type: "success" | "info" | "error" = "info") => {
      if (onShowToast) {
        onShowToast(message, type);
      } else {
        const id = crypto.randomUUID();
        setLocalToasts((prev) => [...prev, { id, message, type }]);
      }
    },
    [onShowToast],
  );

  const lastLibraryErrorRef = useRef("");
  useEffect(() => {
    if (library.error && library.error !== lastLibraryErrorRef.current) {
      handleShowToast(library.error, "error");
    }
    lastLibraryErrorRef.current = library.error;
  }, [library.error, handleShowToast]);

  useEffect(() => {
    setPath("MEMORY.md");
    setDirty(false);
  }, [sessionId, scope]);

  async function changeMode(mode: MemoryMode) {
    setBusy(true);
    try {
      await window.eidosRuntime.memory("memory/policy", {
        sessionId: sessionId ?? null,
        scope,
        mode,
      });
      library.refresh();
    } catch {
      handleShowToast("记忆模式未能保存，请重试。", "error");
    } finally {
      setBusy(false);
    }
  }

  async function exportNotes() {
    setBusy(true);
    try {
      await window.eidosRuntime.exportMemoryNotes(scope, sessionId);
    } catch {
      handleShowToast("导出未完成，请重试。", "error");
    } finally {
      setBusy(false);
    }
  }

  const currentMode = selected?.mode ?? "auto";
  const badgeConfig = modeBadges[currentMode];

  return (
    <div className="settings-panel memory-settings">
      {/* 独立/测试环境下的顶部悬浮条兜底 */}
      {!onShowToast && (
        <ToastContainer toasts={localToasts} onDismiss={dismissToast} />
      )}

      <header className="settings-panel-header">
        <h1>记忆</h1>
        <p className="settings-panel-subtitle">
          查看 Eidos 记住了什么，并直接修改笔记。
        </p>
      </header>

      {/* 顶部控制卡片 */}
      <section className="memory-policy-card">
        <div className="memory-toolbar">
          <div className="memory-toolbar-controls">
            {scopes.length > 1 && (
              <label className="memory-control-group">
                <span className="memory-control-label">记忆范围</span>
                <div className="memory-select-wrapper">
                  <select
                    aria-label="记忆范围"
                    value={scope}
                    disabled={dirty || busy}
                    onChange={(event) =>
                      setScope(event.target.value as MemoryScopeChoice)
                    }
                  >
                    <option value="current">当前项目</option>
                    <option value="global">全局</option>
                  </select>
                </div>
              </label>
            )}

            <label className="memory-control-group">
              <span className="memory-control-label">记忆模式</span>
              <div className="memory-select-wrapper">
                <select
                  aria-label="记忆模式"
                  value={currentMode}
                  disabled={!selected || busy || dirty}
                  onChange={(event) => void changeMode(event.target.value as MemoryMode)}
                >
                  {Object.entries(modes).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <StatusBadge
                tone={badgeConfig.tone}
                className="memory-mode-badge"
              >
                {badgeConfig.text}
              </StatusBadge>
            </label>
          </div>

          <div className="memory-toolbar-actions">
            <Button
              size="small"
              variant="secondary"
              icon={<ExportIcon />}
              disabled={!selected || busy || dirty}
              onClick={() => void exportNotes()}
            >
              导出 Markdown
            </Button>
          </div>
        </div>

        <div className="memory-policy-banner">
          <span className="memory-policy-icon" aria-hidden="true">
            <InfoIcon />
          </span>
          <p className="memory-policy-description">
            {descriptions[currentMode]}
          </p>
        </div>
      </section>

      {/* 现代双栏知识工作台 */}
      <div className="memory-workspace">
        <nav className="memory-files" aria-label="记忆文件">
          <div className="memory-files-tools">
            <div className="memory-search-row">
              <SearchInput
                className="memory-search-input"
                ariaLabel="搜索记忆"
                placeholder="搜索笔记…"
                maxLength={512}
                value={library.query}
                disabled={dirty}
                onChange={(val) => library.setQuery(val)}
              />
            </div>
            <div className="memory-files-subheader">
              <span className="memory-files-count">
                {library.state?.documents ? `${library.state.documents.length + 1} 份笔记` : "笔记索引"}
              </span>
              <Button
                variant="secondary"
                size="small"
                icon={<PlusIcon />}
                disabled={dirty || busy}
                onClick={() => setPath(null)}
              >
                新建笔记
              </Button>
            </div>
          </div>

          <div className="memory-file-list-wrap">
            <button
              type="button"
              className={`memory-file memory-file--index${path === "MEMORY.md" ? " memory-file--active" : ""}`}
              aria-pressed={path === "MEMORY.md"}
              disabled={dirty}
              onClick={() => setPath("MEMORY.md")}
            >
              <div className="memory-file-icon-box" aria-hidden="true">
                <IndexBookmarkIcon />
              </div>
              <div className="memory-file-text-box">
                <div className="memory-file-title-row">
                  <strong>MEMORY.md</strong>
                  <span className="memory-file-pill">索引</span>
                </div>
                <span>记忆索引</span>
              </div>
            </button>

            {library.loading && !library.state && (
              <div className="memory-files-loading" role="status">
                <div className="memory-spinner" />
                <p>正在读取记忆…</p>
              </div>
            )}

            {library.state?.documents.map((note) => (
              <button
                type="button"
                key={note.id}
                className={`memory-file${path === note.path ? " memory-file--active" : ""}`}
                aria-pressed={path === note.path}
                disabled={dirty}
                onClick={() => setPath(note.path)}
              >
                <div className="memory-file-icon-box" aria-hidden="true">
                  <DocumentTextIcon />
                </div>
                <div className="memory-file-text-box">
                  <div className="memory-file-title-row">
                    <strong>{note.path}</strong>
                    {note.archived && <span className="memory-file-pill">已归档</span>}
                  </div>
                  <span>{note.summary}</span>
                </div>
              </button>
            ))}

            {library.state?.documents.length === 0 && (
              <div className="memory-empty-box">
                <span className="memory-empty-icon" aria-hidden="true">
                  {library.query ? <SearchEmptyIcon /> : <NotesEmptyIcon />}
                </span>
                <p className="memory-files-empty">
                  {library.query
                    ? "没有匹配的笔记。"
                    : "这里还没有主题笔记。你可以在对话中说“记住这个偏好”，也可以新建笔记。"}
                </p>
              </div>
            )}

            {library.state?.searchLimited && (
              <p className="memory-files-empty memory-files-limited">
                匹配的笔记较多，请细化关键词以查找其他结果。
              </p>
            )}
          </div>

          {(library.cursor > 0 || library.state?.nextCursor != null) && (
            <div className="memory-file-pages">
              {library.cursor > 0 && (
                <Button
                  variant="ghost"
                  size="small"
                  disabled={dirty}
                  onClick={() => library.setCursor(0)}
                >
                  返回第一页
                </Button>
              )}
              {library.state?.nextCursor != null && (
                <Button
                  variant="ghost"
                  size="small"
                  disabled={dirty}
                  onClick={() => library.setCursor(library.state!.nextCursor!)}
                >
                  下一页
                </Button>
              )}
            </div>
          )}
        </nav>

        <main className="memory-editor-area">
          {selected && (
            <MemoryNoteEditor
              key={`${sessionId}:${selected.id}:${path}`}
              sessionId={sessionId}
              scope={scope}
              path={path}
              generation={selected.generation}
              onDirty={setEditorDirty}
              onSelect={setPath}
              onOpenSource={onOpenSource}
              onShowToast={handleShowToast}
              onSaved={(saved) => {
                setPath(saved);
                library.refresh();
                handleShowToast("记忆已更新", "success");
              }}
            />
          )}
        </main>
      </div>
    </div>
  );
}

function InfoIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  );
}
