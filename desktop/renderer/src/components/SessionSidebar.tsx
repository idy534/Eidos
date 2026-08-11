import { useEffect, useState } from "react";

import type { Session, SessionGitStatus } from "../contracts.js";
import type { RuntimePresentation } from "../session-state.js";
import { groupSessionsByProject, taskStatusPresentation } from "../session-state.js";
import { ContextMenu } from "./DropdownMenu.js";
import { EidosMark } from "./EidosMark.js";
import { PrimaryActionButton } from "./PrimaryActionButton.js";
import settingsIcon from "./settings.svg";


interface Props {
  sessions: Session[];
  selectedId: string | undefined;
  disabled: boolean;
  readCompletedSessions: ReadonlySet<string>;
  /** Real Runtime status presentation — used for the status indicator dot */
  runtimePresentation: RuntimePresentation;
  /** Session ID currently being selected (shows local loading) */
  isSelectingSessionId?: string | undefined;
  gitStatusBySessionId?: ReadonlyMap<string, SessionGitStatus>;
  onCreate: () => void;
  onCreateInProject: (workspaceRoot: string) => void;
  onSelect: (session: Session) => void;
  onRename: (session: Session) => void;
  onDelete: (session: Session) => void;
  onOpenSettings: () => void;
}

interface ContextMenuState {
  session: Session;
  x: number;
  y: number;
  element?: HTMLElement | null;
}

export function SessionSidebar({
  sessions, selectedId, disabled, readCompletedSessions,
  runtimePresentation, isSelectingSessionId, gitStatusBySessionId = new Map(),
  onCreate, onCreateInProject, onSelect, onRename, onDelete, onOpenSettings,
}: Props) {
  const projects = groupSessionsByProject(sessions);
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenuState | undefined>(undefined);

  useEffect(() => {
    if (!contextMenu) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setContextMenu(undefined);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [contextMenu]);

  // Dot CSS class derived from real RuntimePresentation tone
  const dotClass = `runtime-pulse-dot runtime-pulse-dot--${runtimePresentation.tone}${runtimePresentation.animated ? " runtime-pulse-dot--animated" : ""}`;

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
      <PrimaryActionButton
        size="compact"
        label="新建任务"
        shortcut="⌘N"
        disabled={disabled}
        onClick={onCreate}
      />
      <nav aria-label="工作空间与任务">
        <p className="nav-label">项目与任务</p>
        {sessions.length === 0 ? (
          <p className="nav-empty">还没有任务，点击上方按键创建</p>
        ) : (
          <ul className="workspace-list">
            {projects.map((project) => (
              <li key={project.key}>
                <section className="workspace-group" aria-label={project.displayName}>
                  <div className="workspace-title-row" title={project.workspaceRoot}>
                    <button
                      className="workspace-toggle"
                      aria-expanded={!collapsedProjects.has(project.key)}
                      onClick={() => setCollapsedProjects((current) => {
                        const next = new Set(current);
                        if (next.has(project.key)) {
                          next.delete(project.key);
                        } else {
                          next.add(project.key);
                        }
                        return next;
                      })}
                    >
                      <ChevronIcon open={!collapsedProjects.has(project.key)} />
                      <FolderIcon open={!collapsedProjects.has(project.key)} />
                      <span className="workspace-name">{project.displayName}</span>
                    </button>
                    <button
                      className="workspace-add"
                      aria-label={`在 ${project.displayName} 中新建任务`}
                      disabled={disabled}
                      onClick={() => {
                        setCollapsedProjects((current) => {
                          const next = new Set(current);
                          next.delete(project.key);
                          return next;
                        });
                        onCreateInProject(project.workspaceRoot);
                      }}
                    >＋</button>
                  </div>
                  {!collapsedProjects.has(project.key) && (
                    <ul className="session-list">
                      {project.sessions.map((session) => {
                        const status = taskStatusPresentation(
                          session.taskStatus,
                          readCompletedSessions.has(session.id),
                        );
                        const isSelected = session.id === selectedId;
                        const isLoading = session.id === isSelectingSessionId;
                        const gitStatus = gitStatusBySessionId.get(session.id);
                        return (
                          <li className="session-item" key={session.id}>
                            <button
                              className={isSelected ? "selected" : ""}
                              aria-current={isSelected ? "page" : undefined}
                              aria-busy={isLoading}
                              aria-haspopup="menu"
                              disabled={disabled}
                              onClick={() => {
                                setContextMenu(undefined);
                                onSelect(session);
                              }}
                              onContextMenu={(event) => {
                                event.preventDefault();
                                setContextMenu({
                                  session,
                                  x: event.clientX,
                                  y: event.clientY,
                                  element: event.currentTarget,
                                });
                              }}
                              onKeyDown={(event) => {
                                if ((event.shiftKey && event.key === "F10") || event.key === "ContextMenu") {
                                  event.preventDefault();
                                  const bounds = event.currentTarget.getBoundingClientRect();
                                  setContextMenu({
                                    session,
                                    x: bounds.left,
                                    y: bounds.bottom,
                                    element: event.currentTarget,
                                  });
                                }
                              }}
                            >
                              <span className="session-labels">
                                <span className="session-title">{session.title ?? "新任务"}</span>
                                {project.gitAvailable && session.worktree && (
                                  <span className="session-branch">{session.worktree.branch}</span>
                                )}
                              </span>
                              {(isLoading || (project.gitAvailable && gitStatus?.dirty) || status) && (
                                <span className="session-indicators">
                                  {isLoading && (
                                    <span className="session-loading-dot" aria-label="加载中" />
                                  )}
                                  {project.gitAvailable && gitStatus?.dirty && !isLoading && (
                                    <span
                                      className="git-dirty-indicator"
                                      aria-label="有未提交改动"
                                      title="有未提交改动"
                                    />
                                  )}
                                  {status && !isLoading && (
                                    <span
                                      className={`task-indicator task-indicator--${status.tone}${status.spinning ? " task-indicator--spinning" : ""}`}
                                      title={status.label}
                                      aria-label={status.label}
                                    />
                                  )}
                                </span>
                              )}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
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
          {/* Real status dot — no longer hardcoded green */}
          <span
            className={dotClass}
            title={runtimePresentation.label}
            aria-label={runtimePresentation.label}
          />
        </button>
      </div>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          label={`任务操作：${contextMenu.session.title ?? "新任务"}`}
          restoreFocusElement={contextMenu.element}
          onClose={() => setContextMenu(undefined)}
          items={[
            {
              key: "rename",
              label: "编辑标题",
              onClick: () => {
                setContextMenu(undefined);
                onRename(contextMenu.session);
              },
            },
            {
              key: "delete",
              label: "删除任务",
              danger: true,
              disabled: contextMenu.session.taskStatus === "in_progress",
              onClick: () => {
                setContextMenu(undefined);
                onDelete(contextMenu.session);
              },
            },
          ]}
        />
      )}
    </aside>
  );
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
