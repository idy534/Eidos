import { useCallback, useEffect, useRef, useState } from "react";
import type { MemoryScope } from "../../../../shared/memory.generated.js";
import { Button } from "../Button.js";
import { SearchInput } from "../SearchInput.js";
import { ToastContainer, type ToastItem } from "../ToastContainer.js";
import { MemoryNoteEditor } from "./MemoryNoteEditor.js";
import {
  IndexBookmarkIcon,
  DocumentTextIcon,
  PlusIcon,
  SearchEmptyIcon,
  NotesEmptyIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
} from "./MemoryIcons.js";
import { useMemoryDocuments, type MemoryScopeChoice } from "./useMemoryDocuments.js";
import { useMemoryLocation } from "./useMemoryLocation.js";

const modes = { off: "关闭", read: "只读", auto: "自动" } as const;
type MemoryMode = MemoryScope["mode"];

export function MemorySettings(props: {
  onOpenSource?: ((id: string) => void) | undefined;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const location = useMemoryLocation();
  return <MemorySettingsContent key={`${location.projectId}:${location.scope}`} {...props} location={location} />;
}

function MemorySettingsContent({
  location,
  onOpenSource,
  onShowToast,
}: {
  location: ReturnType<typeof useMemoryLocation>;
  onOpenSource?: ((id: string) => void) | undefined;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
}) {
  const { projectId, scope, valid } = location;
  const [path, setPath] = useState<string | null>("MEMORY.md");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [localToasts, setLocalToasts] = useState<ToastItem[]>([]);

  const library = useMemoryDocuments(projectId ?? undefined, scope, valid);
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

  async function changeMode(mode: MemoryMode) {
    setBusy(true);
    try {
      await window.eidosRuntime.memory("memory/policy", {
        projectId,
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
      await window.eidosRuntime.exportMemoryNotes({ scope, projectId });
    } catch {
      handleShowToast("导出未完成，请重试。", "error");
    } finally {
      setBusy(false);
    }
  }

  const currentMode = selected?.mode ?? "auto";

  return (
    <div className="settings-panel memory-settings">
      {/* 独立/测试环境下的顶部悬浮条兜底 */}
      {!onShowToast && (
        <ToastContainer toasts={localToasts} onDismiss={dismissToast} />
      )}

      <header className="settings-panel-header memory-header">
        <div className="memory-header-info">
          <h1>记忆</h1>
          <p className="settings-panel-subtitle">
            查看 Eidos 记住了什么，并直接修改笔记。
          </p>
        </div>

        <div className="memory-header-actions">
          <label className="memory-control-group">
            <span className="memory-control-label">位置</span>
            <div className="memory-select-wrapper memory-location-select">
              <select aria-label="位置" value={projectId ?? ""}
                title={location.projects.find((project) => project.id === projectId)?.workspaceRoot}
                disabled={dirty || busy}
                onChange={(event) => location.selectProject(event.target.value || null)}>
                <option value="">无项目</option>
                {projectId && !valid && <option value={projectId}>{location.loaded ? "项目已不存在" : "正在读取项目…"}</option>}
                {location.projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name ?? project.workspaceRoot.split("/").filter(Boolean).at(-1)} · {project.workspaceRoot}
                  </option>
                ))}
              </select>
            </div>
          </label>

          {projectId ? (
            <label className="memory-control-group">
              <span className="memory-control-label">范围</span>
              <div className="memory-select-wrapper">
                <select aria-label="范围" value={scope} disabled={dirty || busy}
                  onChange={(event) => location.selectScope(event.target.value as MemoryScopeChoice)}>
                  <option value="current">所选项目</option>
                  <option value="global">全局</option>
                </select>
              </div>
            </label>
          ) : (
            <div className="memory-control-group"><span className="memory-control-label">范围</span><span>全局</span></div>
          )}
          <label className="memory-control-group">
            <span className="memory-control-label">模式</span>
            <div className="memory-select-wrapper">
              <select
                aria-label="模式"
                value={currentMode}
                disabled={!valid || !selected || busy || dirty}
                onChange={(event) => void changeMode(event.target.value as MemoryMode)}
              >
                {Object.entries(modes).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          </label>


          <Button
            size="small"
            variant="secondary"
            disabled={!valid || !selected || busy || dirty}
            onClick={() => void exportNotes()}
          >
            导出
          </Button>
        </div>
      </header>

      <div className="memory-location-status">
        {dirty ? <p role="status">请先保存或取消编辑，再切换位置或范围。</p>
          : !projectId ? <p>无项目对话使用全局记忆。</p>
          : scope === "global" ? <p>所有项目和无项目对话共用这份全局记忆。</p> : null}
        {location.error ? <p role="alert">{location.error} <Button size="small" variant="ghost" onClick={location.refresh}>重试项目列表</Button></p>
          : projectId && !valid && <p role="status">{location.loaded ? "所选项目已不存在，请重新选择位置。" : "正在读取项目列表…"}</p>}
        {library.error && <p role="alert">{library.error} <Button size="small" variant="ghost" onClick={library.refresh}>重新读取</Button></p>}
      </div>

      {/* 现代双栏知识工作台 */}
      <div className={`memory-workspace${sidebarCollapsed ? " memory-workspace--sidebar-collapsed" : ""}`}>
        <button
          type="button"
          className={`memory-collapse-toggle${sidebarCollapsed ? " memory-collapse-toggle--collapsed" : ""}`}
          aria-label={sidebarCollapsed ? "展开笔记列表" : "折叠笔记列表"}
          aria-expanded={!sidebarCollapsed}
          title={sidebarCollapsed ? "展开笔记列表" : "折叠笔记列表"}
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
        >
          {sidebarCollapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
        </button>

        <nav
          className="memory-files"
          aria-label="记忆文件"
          aria-hidden={sidebarCollapsed}
        >
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
                disabled={!valid || !selected || dirty || busy}
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
          {selected && (valid || dirty) && (
            <MemoryNoteEditor
              key={`${projectId}:${selected.id}:${path}`}
              projectId={projectId ?? undefined}
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
