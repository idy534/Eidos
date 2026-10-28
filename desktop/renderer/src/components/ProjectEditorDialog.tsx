import { useEffect, useRef, useState } from "react";
import type { Project } from "../contracts.js";
import type { ProjectRootInput } from "../../../shared/project-workspaces.generated.js";
import { Button } from "./Button.js";
import { FieldError } from "./FieldError.js";
import { WorkspaceFolderIcon } from "./WorkspaceFileIcon.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

export interface ProjectEditorDialogProps {
  open: boolean;
  project?: Project | undefined;
  sourceFolder?: string | undefined;
  busy?: boolean | undefined;
  error?: string | undefined;
  onSave(name: string | undefined, roots: ProjectRootInput[], primaryRootId: string): void;
  onSelectFolder?: (() => void) | undefined;
  onCancel(): void;
  onRemove?: (() => void) | undefined;
  canRemove?: boolean | undefined;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
}

const folderName = (path: string) => path.split("/").filter(Boolean).at(-1) || path;

export function ProjectEditorDialog({ open, project, sourceFolder, busy = false, error,
  onSave, onSelectFolder, onCancel, onRemove, canRemove = false, getFallbackFocus,
}: ProjectEditorDialogProps) {
  const [name, setName] = useState("");
  const [roots, setRoots] = useState<ProjectRootInput[]>([]);
  const [primary, setPrimary] = useState("");
  const [folderError, setFolderError] = useState<string>();
  const [selecting, setSelecting] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const openRef = useRef(open);
  openRef.current = open;
  useDialogFocusLifecycle({ open, getFallbackFocus });
  useEffect(() => {
    if (!open) return;
    const initial = project?.roots?.map(({ id, path }) => ({ id, path }))
      ?? (project ? [{ id: `root_${project.id}`, path: project.workspaceRoot }] : []);
    setName(project?.name ?? ""); setRoots(initial);
    setPrimary(project?.primaryRootId ?? initial[0]?.id ?? "");
    setFolderError(undefined);
  }, [open, project?.id, project?.revision]);
  useEffect(() => {
    if (!open || !sourceFolder) return;
    setRoots((previous) => {
      if (previous.some((root) => root.path === sourceFolder)) return previous;
      const next = { id: `root_${crypto.randomUUID()}`, path: sourceFolder };
      if (!previous.length) setPrimary(next.id);
      return [...previous, next];
    });
  }, [open, sourceFolder]);
  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy && !selecting) { event.preventDefault(); onCancel(); }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const controls = [...dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]),input:not([disabled])")];
      const first = controls[0]; const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open, busy, selecting, onCancel]);
  async function addFolder() {
    if (onSelectFolder) { onSelectFolder(); return; }
    setSelecting(true); setFolderError(undefined);
    try {
      const path = await window.eidosRuntime.selectWorkspace();
      if (!path || !openRef.current) return;
      if (roots.some((root) => root.path === path)) { setFolderError("这个文件夹已经在项目中。"); return; }
      const next = { id: `root_${crypto.randomUUID()}`, path };
      setRoots((previous) => [...previous, next]);
      if (!roots.length) setPrimary(next.id);
    } catch { setFolderError("无法选择文件夹，请重试。"); }
    finally { setSelecting(false); }
  }
  if (!open) return null;
  const disabled = busy || selecting;
  const title = project ? "编辑项目" : "创建项目";
  return <div className="modal-backdrop" onClick={disabled ? undefined : onCancel}>
    <div ref={dialogRef} className="modal-dialog create-project-dialog project-editor-dialog" role="dialog" aria-modal="true"
      aria-labelledby="project-editor-title" onClick={(event) => event.stopPropagation()}>
      <div className="create-project-dialog-header">
        <h3 id="project-editor-title">{title}</h3>
        <button className="create-project-dialog-close" type="button" aria-label="关闭" disabled={disabled} onClick={onCancel}>×</button>
      </div>
      <div className="create-project-dialog-body">
        <label className="create-project-name-field">
          <span className="project-editor-folder-icon"><WorkspaceFolderIcon /></span>
          <span className="sr-only">项目名称（可选）</span>
          <input aria-label="项目名称（可选）" placeholder="项目名称（可选）" value={name} maxLength={120}
            disabled={disabled} onChange={(event) => setName(event.target.value)} />
        </label>
        <div className="create-project-source">
          <h4>源文件夹</h4>
          <div className="project-editor-folders">
            <ul aria-label="项目文件夹">
              {roots.map((root) => <li key={root.id} className="project-editor-folder-row" title={root.path}>
                <span className="project-editor-folder-icon"><WorkspaceFolderIcon /></span>
                <span className="project-editor-folder-name">{folderName(root.path)}<span className="project-editor-folder-path">{root.path}</span></span>
                {root.id === primary ? <span className="project-editor-primary">主要</span>
                  : <button className="project-editor-make-primary" type="button" disabled={disabled} onClick={() => setPrimary(root.id)} aria-label={`将 ${folderName(root.path)} 设为主要`}>设为主要</button>}
                <button className="project-editor-remove-folder" type="button" disabled={disabled || roots.length === 1}
                  aria-label={`移除文件夹 ${folderName(root.path)}`} onClick={() => {
                    const remaining = roots.filter((entry) => entry.id !== root.id);
                    setRoots(remaining); if (root.id === primary) setPrimary(remaining[0]?.id ?? "");
                  }}>×</button>
              </li>)}
            </ul>
            <button type="button" className="project-editor-add-folder" disabled={disabled} onClick={() => void addFolder()}
              aria-label={roots.length ? "添加文件夹" : "添加 Eidos 可读写的文件夹"}>
              <span className="project-editor-folder-icon"><WorkspaceFolderIcon /></span><span>添加文件夹</span>
            </button>
          </div>
        </div>
        {project && <p className="project-editor-note">文件夹配置用于新聊天。已有聊天保留原工作目录。</p>}
        {(error || folderError) && <FieldError error={error || folderError} />}
      </div>
      <div className="create-project-dialog-footer project-editor-footer">
        {onRemove && <Button variant="danger" disabled={disabled || !canRemove} onClick={onRemove}
          title={canRemove ? undefined : "项目仍有聊天或工作区，当前无法移除。"}>移除本地项目</Button>}
        <span className="project-editor-footer-spacer" />
        <Button variant="ghost" disabled={disabled} onClick={onCancel}>取消</Button>
        <Button variant="primary" loading={busy} disabled={disabled || !roots.length || !roots.some((root) => root.id === primary)}
          onClick={() => onSave(name.trim() || undefined, roots, primary)}>{project ? "保存" : "创建项目"}</Button>
      </div>
    </div>
  </div>;
}
