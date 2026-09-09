import { useEffect, useRef, useState } from "react";

import type { Project, Session, SessionGitStatus } from "../contracts.js";
import type { ProjectSessionGroup, RuntimePresentation } from "../session-state.js";
import { groupSessionsByProject, taskStatusPresentation } from "../session-state.js";
import { ContextMenu } from "./DropdownMenu.js";
import { EidosMark } from "./EidosMark.js";
import { PrimaryActionButton } from "./PrimaryActionButton.js";
import settingsIcon from "./settings.svg";

export const COLLAPSED_PROJECTS_KEY = "eidos.sidebarCollapsedProjects";

export function loadCollapsedProjects(): Set<string> {
  try {
    const raw = window.localStorage.getItem(COLLAPSED_PROJECTS_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(
      Array.isArray(parsed) ? parsed.filter((key): key is string => typeof key === "string") : [],
    );
  } catch {
    return new Set();
  }
}

export function saveCollapsedProjects(collapsed: ReadonlySet<string>): void {
  try {
    window.localStorage.setItem(COLLAPSED_PROJECTS_KEY, JSON.stringify([...collapsed]));
  } catch {
    // localStorage failure is non-critical
  }
}

interface Props {
  sessions: Session[];
  projects: Project[];
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
  onDeleteProject: (project: Project) => void;
  onShowInFinder?: (project: Project) => void;
  onOpenSettings: () => void;
  navigationSlot?: React.ReactNode;
}

type ContextMenuState =
  | {
      kind: "session";
      session: Session;
      x: number;
      y: number;
      element?: HTMLElement | null;
    }
  | {
      kind: "project";
      project: Project;
      hasSessions: boolean;
      x: number;
      y: number;
      element?: HTMLElement | null;
    };

export function SessionSidebar({
  sessions, projects: catalogProjects, selectedId, disabled, readCompletedSessions,
  runtimePresentation, isSelectingSessionId, gitStatusBySessionId = new Map(),
  onCreate, onCreateInProject, onSelect, onRename, onDelete, onDeleteProject, onShowInFinder, onOpenSettings,
  navigationSlot,
}: Props) {
  const visibleSessions = sessions.filter(
    (session) => session.taskStatus !== "new" || Boolean(session.title?.trim()),
  );
  const projects = groupSessionsByProject(visibleSessions, catalogProjects);
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(loadCollapsedProjects);
  const prevSelectedIdRef = useRef<string | undefined>(selectedId);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | undefined>(undefined);

  const toggleProject = (projectKey: string) => {
    setCollapsedProjects((current) => {
      const next = new Set(current);
      if (next.has(projectKey)) {
        next.delete(projectKey);
      } else {
        next.add(projectKey);
      }
      saveCollapsedProjects(next);
      return next;
    });
  };

  useEffect(() => {
    if (selectedId && selectedId !== prevSelectedIdRef.current) {
      prevSelectedIdRef.current = selectedId;
      const activeProject = projects.find((project) =>
        project.sessions.some((session) => session.id === selectedId),
      );
      if (activeProject && collapsedProjects.has(activeProject.key)) {
        setCollapsedProjects((current) => {
          if (!current.has(activeProject.key)) return current;
          const next = new Set(current);
          next.delete(activeProject.key);
          saveCollapsedProjects(next);
          return next;
        });
      }
    } else {
      prevSelectedIdRef.current = selectedId;
    }
  }, [selectedId, projects, collapsedProjects]);

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
      {navigationSlot && <div className="sidebar-top-bar">{navigationSlot}</div>}
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
        label="新建会话"
        shortcut="⌘N"
        disabled={disabled}
        onClick={onCreate}
      />
      <nav aria-label="项目与最近">
        <p className="nav-label">项目</p>
        {projects.length === 0 ? (
          <p className="nav-empty">还没有任务，点击上方按键创建</p>
        ) : (
          <ul className="workspace-list">
            {projects.map((project) => {
              const isExpanded = !collapsedProjects.has(project.key);
              return (
                <li key={project.key}>
                  <section className={`workspace-group${project.projectless ? " workspace-group--recent" : ""}`} aria-label={project.displayName}>
                    <div className="workspace-title-row" title={project.workspaceRoot}>
                      {project.projectless ? (
                        <button
                          className="workspace-toggle workspace-toggle--recent"
                          aria-expanded={isExpanded}
                          onClick={() => toggleProject(project.key)}
                        >
                          <span className="workspace-name">{project.displayName}</span>
                          <ChevronIcon open={isExpanded} />
                        </button>
                      ) : (
                        <button
                          className="workspace-toggle"
                          aria-expanded={isExpanded}
                          aria-haspopup={project.project ? "menu" : undefined}
                          onClick={() => toggleProject(project.key)}
                          onContextMenu={(event) => {
                            if (!project.project) return;
                            event.preventDefault();
                            const hasSessions = project.sessions.length > 0
                              || sessions.some((session) => session.project?.id === project.project?.id);
                            setContextMenu({
                              kind: "project",
                              project: project.project,
                              hasSessions,
                              x: event.clientX,
                              y: event.clientY,
                              element: event.currentTarget,
                            });
                          }}
                          onKeyDown={(event) => {
                            if (!project.project || !(event.key === "ContextMenu" || (event.shiftKey && event.key === "F10"))) {
                              return;
                            }
                            event.preventDefault();
                            const bounds = event.currentTarget.getBoundingClientRect();
                            const hasSessions = project.sessions.length > 0
                              || sessions.some((session) => session.project?.id === project.project?.id);
                            setContextMenu({
                              kind: "project",
                              project: project.project,
                              hasSessions,
                              x: bounds.left,
                              y: bounds.bottom,
                              element: event.currentTarget,
                            });
                          }}
                        >
                          <FolderIcon open={isExpanded} />
                          <span className="workspace-name">{project.displayName}</span>
                        </button>
                      )}
                      {!project.projectless && (
                        <button
                          className="workspace-add"
                          aria-label={`在 ${project.displayName} 中新建会话`}
                          disabled={disabled}
                          onClick={() => {
                            setCollapsedProjects((current) => {
                              const next = new Set(current);
                              next.delete(project.key);
                              saveCollapsedProjects(next);
                              return next;
                            });
                            onCreateInProject(project.workspaceRoot);
                          }}
                        >＋</button>
                      )}
                    </div>
                    <div
                      className={`workspace-collapse ${isExpanded ? "workspace-collapse--expanded" : ""}`}
                      aria-hidden={!isExpanded}
                      style={{ visibility: isExpanded ? "visible" : "hidden" }}
                    >
                      <div className="workspace-collapse-inner">
                        <ProjectSessionList
                          project={project}
                          isExpanded={isExpanded}
                          selectedId={selectedId}
                          disabled={disabled}
                          readCompletedSessions={readCompletedSessions}
                          isSelectingSessionId={isSelectingSessionId}
                          gitStatusBySessionId={gitStatusBySessionId}
                          onSelect={onSelect}
                          onDismissContextMenu={() => setContextMenu(undefined)}
                          onOpenSessionContextMenu={(session, coords, element) => {
                            setContextMenu({
                              kind: "session",
                              session,
                              x: coords.x,
                              y: coords.y,
                              element,
                            });
                          }}
                        />
                      </div>
                    </div>
                  </section>
                </li>
              );
            })}
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

      {contextMenu?.kind === "session" && (
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
      {contextMenu?.kind === "project" && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          label={`项目操作：${contextMenu.project.name ?? contextMenu.project.workspaceRoot}`}
          restoreFocusElement={contextMenu.element}
          onClose={() => setContextMenu(undefined)}
          items={[
            {
              key: "show-in-finder",
              label: "在 Finder 中显示",
              onClick: () => {
                setContextMenu(undefined);
                onShowInFinder?.(contextMenu.project);
              },
            },
            {
              key: "delete-project",
              label: "删除项目",
              danger: true,
              disabled: contextMenu.hasSessions,
              onClick: () => {
                setContextMenu(undefined);
                onDeleteProject(contextMenu.project);
              },
            },
          ]}
        />
      )}
    </aside>
  );
}

export const DEFAULT_VISIBLE_SESSION_COUNT = 6;

interface ProjectSessionListProps {
  project: ProjectSessionGroup;
  isExpanded: boolean;
  selectedId: string | undefined;
  disabled: boolean;
  readCompletedSessions: ReadonlySet<string>;
  isSelectingSessionId?: string | undefined;
  gitStatusBySessionId: ReadonlyMap<string, SessionGitStatus>;
  onSelect: (session: Session) => void;
  onDismissContextMenu: () => void;
  onOpenSessionContextMenu: (
    session: Session,
    coords: { x: number; y: number },
    element: HTMLElement,
  ) => void;
}

function ProjectSessionList({
  project,
  isExpanded,
  selectedId,
  disabled,
  readCompletedSessions,
  isSelectingSessionId,
  gitStatusBySessionId,
  onSelect,
  onDismissContextMenu,
  onOpenSessionContextMenu,
}: ProjectSessionListProps) {
  const [showAll, setShowAll] = useState(false);
  const [prevExpanded, setPrevExpanded] = useState(isExpanded);

  if (prevExpanded !== isExpanded) {
    setPrevExpanded(isExpanded);
    if (!isExpanded) {
      setShowAll(false);
    }
  }

  if (project.sessions.length === 0) {
    return <p className="session-empty">暂无会话</p>;
  }

  const hasMore = project.sessions.length > DEFAULT_VISIBLE_SESSION_COUNT;
  const visibleSessions = hasMore && !showAll
    ? project.sessions.slice(0, DEFAULT_VISIBLE_SESSION_COUNT)
    : project.sessions;

  return (
    <>
      <ul className="session-list">
        {visibleSessions.map((session) => {
          const status = taskStatusPresentation(
            session.taskStatus,
            readCompletedSessions.has(session.id),
            session.activeRunStatus,
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
                  onDismissContextMenu();
                  onSelect(session);
                }}
                onContextMenu={(event) => {
                  event.preventDefault();
                  onOpenSessionContextMenu(
                    session,
                    { x: event.clientX, y: event.clientY },
                    event.currentTarget,
                  );
                }}
                onKeyDown={(event) => {
                  if ((event.shiftKey && event.key === "F10") || event.key === "ContextMenu") {
                    event.preventDefault();
                    const bounds = event.currentTarget.getBoundingClientRect();
                    onOpenSessionContextMenu(
                      session,
                      { x: bounds.left, y: bounds.bottom },
                      event.currentTarget,
                    );
                  }
                }}
              >
                <span className="session-labels">
                  <span className="session-title">{session.title ?? "新任务"}</span>
                  {project.gitAvailable && session.worktree && (
                    <span className="session-branch">
                      {session.worktree.branch ?? "Detached HEAD"}
                    </span>
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
      {hasMore && !showAll && (
        <button
          type="button"
          className="session-expand-all"
          onClick={() => setShowAll(true)}
        >
          展开全部
        </button>
      )}
    </>
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
