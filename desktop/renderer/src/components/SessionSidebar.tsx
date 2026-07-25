import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import type { Session } from "../contracts";
import { groupSessionsByWorkspace, taskStatusPresentation } from "../session-state";
import { EidosMark } from "./EidosMark";
import settingsIcon from "./settings.svg";


interface Props {
  sessions: Session[];
  selectedId: string | undefined;
  disabled: boolean;
  readCompletedSessions: ReadonlySet<string>;
  onCreate: () => void;
  onCreateInWorkspace: (workspaceRoot: string) => void;
  onSelect: (session: Session) => void;
  onRename: (session: Session) => void;
  onDelete: (session: Session) => void;
  onOpenSettings: () => void;
}

export function SessionSidebar({
  sessions, selectedId, disabled, readCompletedSessions,
  onCreate, onCreateInWorkspace, onSelect, onRename, onDelete, onOpenSettings,
}: Props) {
  const workspaces = groupSessionsByWorkspace(sessions);
  const [collapsedWorkspaces, setCollapsedWorkspaces] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenu>();
  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(undefined);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [contextMenu]);
  return (
    <aside className="sidebar" aria-label="任务导航">
      <div className="brand-row">
        <span className="brand-mark" aria-hidden="true">
          <EidosMark />
        </span>
        <div className="brand-titles">
          <span className="brand-name">Eidos</span>
          <span className="brand-badge">Desktop</span>
        </div>
      </div>
      <button className="new-session" disabled={disabled} onClick={onCreate}>
        <span className="new-session-label">＋ 新建任务</span>
        <kbd className="new-session-kbd">⌘N</kbd>
      </button>
      <nav aria-label="工作空间与任务">
        <p className="nav-label">项目与任务</p>
        {sessions.length === 0 ? (
          <p className="nav-empty">还没有任务，点击上方按键创建</p>
        ) : (
          <ul className="workspace-list">
            {workspaces.map((workspace) => (
              <li key={workspace.workspaceRoot}>
                <section className="workspace-group" aria-label={basename(workspace.workspaceRoot)}>
                  <div className="workspace-title-row" title={workspace.workspaceRoot}>
                    <button
                      className="workspace-toggle"
                      aria-expanded={!collapsedWorkspaces.has(workspace.workspaceRoot)}
                      onClick={() => setCollapsedWorkspaces((current) => {
                        const next = new Set(current);
                        if (next.has(workspace.workspaceRoot)) {
                          next.delete(workspace.workspaceRoot);
                        } else {
                          next.add(workspace.workspaceRoot);
                        }
                        return next;
                      })}
                    >
                      <ChevronIcon open={!collapsedWorkspaces.has(workspace.workspaceRoot)} />
                      <FolderIcon open={!collapsedWorkspaces.has(workspace.workspaceRoot)} />
                      <span className="workspace-name">{basename(workspace.workspaceRoot)}</span>
                    </button>
                    <button
                      className="workspace-add"
                      aria-label={`在 ${basename(workspace.workspaceRoot)} 中新建任务`}
                      disabled={disabled}
                      onClick={() => {
                        setCollapsedWorkspaces((current) => {
                          const next = new Set(current);
                          next.delete(workspace.workspaceRoot);
                          return next;
                        });
                        onCreateInWorkspace(workspace.workspaceRoot);
                      }}
                    >＋</button>
                  </div>
                  {!collapsedWorkspaces.has(workspace.workspaceRoot) && <ul className="session-list">
                    {workspace.sessions.map((session) => {
                      const status = taskStatusPresentation(
                        session.taskStatus,
                        readCompletedSessions.has(session.id),
                      );
                      const isSelected = session.id === selectedId;
                      return <li className="session-item" key={session.id}>
                        <button
                          className={isSelected ? "selected" : ""}
                          aria-current={isSelected ? "page" : undefined}
                          aria-haspopup="menu"
                          disabled={disabled}
                          onClick={() => {
                            setContextMenu(undefined);
                            onSelect(session);
                          }}
                          onContextMenu={(event) => {
                            event.preventDefault();
                            setContextMenu({ session, x: event.clientX, y: event.clientY });
                          }}
                          onKeyDown={(event) => {
                            if (event.shiftKey && event.key === "F10") {
                              event.preventDefault();
                              const bounds = event.currentTarget.getBoundingClientRect();
                              setContextMenu({ session, x: bounds.left, y: bounds.bottom });
                            }
                          }}
                        >
                          <span className="session-title">{session.title ?? "新任务"}</span>
                          {status && (
                            <span
                              className={`task-indicator task-indicator--${status.tone}${status.spinning ? " task-indicator--spinning" : ""}`}
                              title={status.label}
                              aria-label={status.label}
                            />
                          )}
                        </button>
                      </li>
                    })}
                  </ul>}
                </section>
              </li>
            ))}
          </ul>
        )}
      </nav>
      <div className="sidebar-footer">
        <button className="settings-entry" onClick={onOpenSettings} aria-label="打开设置">
          <img src={settingsIcon} alt="" className="settings-icon" />
          <span>设置</span>
          <span className="runtime-pulse-dot" title="Runtime 就绪" />
        </button>
      </div>
      {contextMenu && createPortal(
        <div
          className="task-context-menu"
          role="menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button role="menuitem" onClick={() => { setContextMenu(undefined); onRename(contextMenu.session); }}>编辑标题</button>
          <button role="menuitem" className="danger-action" disabled={contextMenu.session.taskStatus === "in_progress"} onClick={() => { setContextMenu(undefined); onDelete(contextMenu.session); }}>删除任务</button>
        </div>,
        document.body,
      )}
    </aside>
  );
}

interface ContextMenu {
  session: Session;
  x: number;
  y: number;
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

function FolderIcon({ open }: { open: boolean }) {
  return open ? (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="folder-icon folder-icon--open">
      <path d="M2.5 4.75C2.5 3.78 3.28 3 4.25 3H7.8C8.3 3 8.77 3.22 9.08 3.6L10.3 5H15.75C16.72 5 17.5 5.78 17.5 6.75V8H2.5V4.75Z" fill="currentColor" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M2 9.25C2 8.56 2.56 8 3.25 8H16.75C17.44 8 18 8.56 18 9.25L17.15 15.5C17.02 16.36 16.28 17 15.41 17H4.59C3.72 17 2.98 16.36 2.85 15.5L2 9.25Z" fill="currentColor" fillOpacity="0.15" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  ) : (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="folder-icon">
      <path d="M2.5 4.75C2.5 3.78 3.28 3 4.25 3H7.8C8.3 3 8.77 3.22 9.08 3.6L10.3 5H15.75C16.72 5 17.5 5.78 17.5 6.75V15.25C17.5 16.22 16.72 17 15.75 17H4.25C3.28 17 2.5 16.22 2.5 15.25V4.75Z" fill="currentColor" fillOpacity="0.18" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      className={`sidebar-chevron ${open ? "sidebar-chevron--open" : ""}`}
      viewBox="0 0 16 16"
      aria-hidden="true"
    >
      <path d="M6 3.5L10.5 8L6 12.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
