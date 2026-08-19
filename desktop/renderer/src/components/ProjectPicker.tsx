import { useEffect, useLayoutEffect, useState, type CSSProperties, type RefObject } from "react";

import type { Project } from "../contracts.js";
import { useDialogFocusLifecycle } from "./useDialogFocusLifecycle.js";

interface ProjectPickerProps {
  open: boolean;
  projects: Project[];
  selectedProjectId?: string | undefined;
  anchorRef?: RefObject<HTMLElement | null> | undefined;
  onSelect: (project: Project) => void;
  onCreate: () => void;
  onClose: () => void;
  getFallbackFocus?: (() => HTMLElement | null) | undefined;
}

export function ProjectPicker({
  open,
  projects,
  selectedProjectId,
  anchorRef,
  onSelect,
  onCreate,
  onClose,
  getFallbackFocus,
}: ProjectPickerProps) {
  const [query, setQuery] = useState("");
  const [anchorPosition, setAnchorPosition] = useState<{ left: number; bottom: number } | undefined>(undefined);

  useDialogFocusLifecycle({
    open,
    getFallbackFocus,
  });

  useLayoutEffect(() => {
    if (!open || !anchorRef?.current) {
      setAnchorPosition(undefined);
      return;
    }

    const updatePosition = () => {
      const anchor = anchorRef.current?.closest<HTMLElement>(".composer") ?? anchorRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      const pickerWidth = 18 * 16;
      const viewportPadding = 12;
      const maxLeft = Math.max(viewportPadding, window.innerWidth - pickerWidth - viewportPadding);
      setAnchorPosition({
        left: Math.min(Math.max(rect.left, viewportPadding), maxLeft),
        bottom: Math.max(viewportPadding, window.innerHeight - rect.top),
      });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [anchorRef, open]);

  useEffect(() => {
    if (open) setQuery("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleProjects = projects.filter((project) => {
    const label = project.name?.trim() || basename(project.workspaceRoot);
    return !normalizedQuery
      || label.toLocaleLowerCase().includes(normalizedQuery)
      || project.workspaceRoot.toLocaleLowerCase().includes(normalizedQuery);
  });
  const pickerStyle = anchorPosition
    ? {
        "--project-picker-left": `${anchorPosition.left}px`,
        "--project-picker-bottom": `${anchorPosition.bottom}px`,
      } as CSSProperties
    : undefined;

  return (
    <div
      className={`project-picker-layer${anchorPosition ? " project-picker-layer--anchored" : ""}`}
      style={pickerStyle}
      onClick={onClose}
    >
      <div
        className="project-picker-popover"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-picker-title"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="project-picker-title" className="sr-only">选择项目</h2>
        <div className="project-picker-search-row">
          <SearchIcon />
          <input
            type="search"
            aria-label="搜索项目"
            placeholder="搜索项目"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="button" className="project-picker-close" aria-label="关闭" onClick={onClose}>×</button>
        </div>

        <div className="project-picker-list" role="listbox" aria-label="项目列表">
          {visibleProjects.map((project) => {
            const label = project.name?.trim() || basename(project.workspaceRoot);
            const selected = project.id === selectedProjectId;
            return (
              <button
                key={project.id}
                type="button"
                role="option"
                aria-label={label}
                aria-selected={selected}
                className={`project-picker-item${selected ? " project-picker-item--selected" : ""}`}
                onClick={() => onSelect(project)}
              >
                {selected ? <HeartIcon /> : <FolderIcon />}
                <span>{label}</span>
              </button>
            );
          })}
          {visibleProjects.length === 0 && (
            <p className="project-picker-empty">没有匹配的项目</p>
          )}
        </div>

        <button type="button" className="project-picker-create" onClick={onCreate}>
          <PlusIcon />
          <span>新建项目</span>
        </button>
      </div>
    </div>
  );
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

function SearchIcon() {
  return (
    <svg className="project-picker-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="10.8" cy="10.8" r="6.8" stroke="currentColor" strokeWidth="1.6" />
      <path d="m16 16 5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg className="project-picker-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M3.5 6.5c0-1.1.9-2 2-2h5l2 2h6c1.1 0 2 .9 2 2v8.5c0 1.1-.9 2-2 2h-13c-1.1 0-2-.9-2-2V6.5Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

function HeartIcon() {
  return (
    <svg className="project-picker-icon project-picker-icon--selected" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M20.4 5.8a5.1 5.1 0 0 0-7.2 0L12 7l-1.2-1.2a5.1 5.1 0 0 0-7.2 7.2L12 21l8.4-8a5.1 5.1 0 0 0 0-7.2Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg className="project-picker-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 4v16M4 12h16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
