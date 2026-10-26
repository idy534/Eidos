import { useCallback, useEffect, useRef, useState } from "react";

import type { Project, Session, SessionGitStatus } from "../contracts.js";
import type { ProjectSessionGroup, RuntimePresentation } from "../session-state.js";
import { groupSessionsByProject, projectSidebarGroups, sidebarTargetKey, taskStatusPresentation } from "../session-state.js";
import type { SidebarTarget } from "../../../shared/sidebar.js";
import { ContextMenu } from "./DropdownMenu.js";
import { FinderIcon, EditIcon, TrashIcon, PinIcon, AutomationsIcon, CopyIcon } from "./MenuIcons.js";
import { SearchIcon } from "./FindBar.js";
import { EidosMark } from "./EidosMark.js";
import { ForkIcon } from "./ForkIcon.js";
import { PrimaryActionButton } from "./PrimaryActionButton.js";
import { SidebarTooltip, type SidebarTooltipState, type SidebarTooltipTarget } from "./SidebarTooltip.js";
import { Tooltip } from "./Tooltip.js";
import settingsIcon from "./settings.svg";

export const COLLAPSED_PROJECTS_KEY = "eidos.sidebarCollapsedProjects";

function tooltipHasFocus(): boolean {
  return document.getElementById("sidebar-hover-tooltip")?.contains(document.activeElement) ?? false;
}

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
  collapsed?: boolean;
  sessions: Session[];
  projects: Project[];
  pins?: readonly SidebarTarget[];
  pinningDisabled?: boolean;
  pendingPinTargets?: ReadonlySet<string>;
  onSetPinned?: (target: SidebarTarget, pinned: boolean) => void;
  selectedId: string | undefined;
  disabled: boolean;
  readCompletedSessions: ReadonlySet<string>;
  /** Real Runtime status presentation — used for the status indicator dot */
  runtimePresentation: RuntimePresentation;
  /** Session ID currently being selected (shows local loading) */
  isSelectingSessionId?: string | undefined;
  gitStatusBySessionId?: ReadonlyMap<string, SessionGitStatus>;
  onSearch?: (() => void) | undefined;
  onCreate: () => void;
  onCreateInProject: (workspaceRoot: string) => void;
  onSelect: (session: Session) => void;
  onRename: (session: Session) => void;
  onDelete: (session: Session) => void;
  onDeleteProject: (project: Project) => void;
  onShowInFinder?: (project: Project) => void;
  onOpenSettings: () => void;
  onOpenAutomations?: () => void;
  onOpenAutomation?: (id: string) => void;
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
  collapsed = false,
  sessions, projects: catalogProjects, selectedId, disabled, readCompletedSessions,
  pins = [], pinningDisabled = false, pendingPinTargets, onSetPinned,
  runtimePresentation, isSelectingSessionId, gitStatusBySessionId = new Map(),
  onCreate, onCreateInProject, onSelect, onRename, onDelete, onDeleteProject, onShowInFinder, onOpenSettings,
  navigationSlot, onOpenAutomations, onOpenAutomation, onSearch,
}: Props) {
  const visibleSessions = sessions.filter(
    (session) => session.taskStatus !== "new" || Boolean(session.title?.trim()) || Boolean(session.forkedFrom),
  );
  const projects = groupSessionsByProject(visibleSessions, catalogProjects);
  const sidebar = projectSidebarGroups(projects, pins);
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(loadCollapsedProjects);
  const prevSelectedIdRef = useRef<string | undefined>(selectedId);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | undefined>(undefined);
  const [activeTooltip, setActiveTooltip] = useState<SidebarTooltipState | null>(null);
  const enterTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const leaveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isHoveringTriggerRef = useRef(false);
  const isHoveringTooltipRef = useRef(false);
  const navRef = useRef<HTMLElement | null>(null);
  const pinFocusRef = useRef<{ target: SidebarTarget; pinned: boolean; element: HTMLElement | null; started: boolean } | null>(null);

  const requestPin = (target: SidebarTarget, element?: HTMLElement | null) => {
    const pinned = !pins.some((pin) => sidebarTargetKey(pin) === sidebarTargetKey(target));
    pinFocusRef.current = { target, pinned, element: element ?? null, started: false };
    setContextMenu(undefined);
    onSetPinned?.(target, pinned);
  };

  useEffect(() => {
    const request = pinFocusRef.current;
    if (!request) return;
    if (pendingPinTargets?.has(sidebarTargetKey(request.target))) request.started = true;
    const confirmed = pins.some((pin) => sidebarTargetKey(pin) === sidebarTargetKey(request.target)) === request.pinned;
    if (!confirmed) {
      if (request.started && !pendingPinTargets?.has(sidebarTargetKey(request.target))) pinFocusRef.current = null;
      return;
    }
    pinFocusRef.current = null;
    if (document.activeElement !== document.body && document.activeElement !== request.element) return;
    const selector = request.target.kind === "session" ? "[data-session-id]" : "[data-project-id]";
    const row = [...(navRef.current?.querySelectorAll<HTMLElement>(selector) ?? [])].find((element) => (
      request.target.kind === "session" ? element.dataset.sessionId : element.dataset.projectId
    ) === request.target.id);
    const hiddenInProject = row?.closest(".workspace-collapse")?.getAttribute("aria-hidden") === "true";
    const focusTarget = hiddenInProject
      ? row?.closest(".workspace-group")?.querySelector<HTMLElement>(".workspace-toggle")
      : row?.querySelector<HTMLElement>(request.target.kind === "session" ? "button" : ".workspace-toggle");
    focusTarget?.focus({ preventScroll: true });
  }, [pins, pendingPinTargets]);

  const dismissTooltipImmediately = useCallback(() => {
    if (enterTimeoutRef.current) {
      clearTimeout(enterTimeoutRef.current);
      enterTimeoutRef.current = null;
    }
    if (leaveTimeoutRef.current) {
      clearTimeout(leaveTimeoutRef.current);
      leaveTimeoutRef.current = null;
    }
    isHoveringTriggerRef.current = false;
    isHoveringTooltipRef.current = false;
    setActiveTooltip(null);
  }, []);

  const handleTriggerEnter = useCallback(
    (tooltip: SidebarTooltipTarget, element: HTMLElement) => {
      if (contextMenu) return;
      isHoveringTriggerRef.current = true;
      if (leaveTimeoutRef.current) {
        clearTimeout(leaveTimeoutRef.current);
        leaveTimeoutRef.current = null;
      }

      if (activeTooltip) {
        const isSame =
          activeTooltip.kind === tooltip.kind &&
          ((activeTooltip.kind === "session" &&
            tooltip.kind === "session" &&
            activeTooltip.session.id === tooltip.session.id) ||
            (activeTooltip.kind === "project" &&
              tooltip.kind === "project" &&
              activeTooltip.projectName === tooltip.projectName &&
              activeTooltip.workspaceRoot === tooltip.workspaceRoot));
        if (isSame) {
          if (enterTimeoutRef.current) {
            clearTimeout(enterTimeoutRef.current);
            enterTimeoutRef.current = null;
          }
          return;
        }
      }

      const rect = element.getBoundingClientRect();
      if (enterTimeoutRef.current) {
        clearTimeout(enterTimeoutRef.current);
      }
      const delay = activeTooltip ? 60 : 200;
      enterTimeoutRef.current = setTimeout(() => {
        setActiveTooltip({ ...tooltip, rect } as SidebarTooltipState);
      }, delay);
    },
    [activeTooltip, contextMenu],
  );

  const handleTriggerLeave = useCallback(() => {
    isHoveringTriggerRef.current = false;
    if (enterTimeoutRef.current) {
      clearTimeout(enterTimeoutRef.current);
      enterTimeoutRef.current = null;
    }
    if (leaveTimeoutRef.current) {
      clearTimeout(leaveTimeoutRef.current);
    }
    leaveTimeoutRef.current = setTimeout(() => {
      if (!isHoveringTooltipRef.current && !isHoveringTriggerRef.current && !tooltipHasFocus()) {
        setActiveTooltip(null);
      }
    }, 240);
  }, []);

  const handleTooltipEnter = useCallback(() => {
    isHoveringTooltipRef.current = true;
    if (leaveTimeoutRef.current) {
      clearTimeout(leaveTimeoutRef.current);
      leaveTimeoutRef.current = null;
    }
  }, []);

  const handleTooltipLeave = useCallback(() => {
    isHoveringTooltipRef.current = false;
    if (leaveTimeoutRef.current) {
      clearTimeout(leaveTimeoutRef.current);
    }
    leaveTimeoutRef.current = setTimeout(() => {
      if (!isHoveringTooltipRef.current && !isHoveringTriggerRef.current && !tooltipHasFocus()) {
        setActiveTooltip(null);
      }
    }, 240);
  }, []);

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
      if (pins.some((pin) => pin.kind === "session" && pin.id === selectedId)) return;
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
  }, [selectedId, projects, collapsedProjects, pins]);

  // Only session navigation requests a reveal. Project browsing keeps its scroll position.
  useEffect(() => {
    if (!selectedId) return;

    const scrollToSelected = () => {
      const container = navRef.current;
      const activeElement = container?.querySelector<HTMLElement>(".session-item button.selected");
      if (!activeElement || !container) return;
      if (activeElement.closest<HTMLElement>("[data-session-id]")?.dataset.sessionId !== selectedId) return;

      const elemRect = activeElement.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();

      if (containerRect.height > 0) {
        const isFullyVisible =
          elemRect.top >= containerRect.top + 8 &&
          elemRect.bottom <= containerRect.bottom - 8;

        if (!isFullyVisible && typeof activeElement.scrollIntoView === "function") {
          activeElement.scrollIntoView({
            behavior: "smooth",
            block: "center",
            inline: "nearest",
          });
        }
      } else if (typeof activeElement.scrollIntoView === "function") {
        activeElement.scrollIntoView({
          behavior: "smooth",
          block: "center",
          inline: "nearest",
        });
      }
    };

    const frame = requestAnimationFrame(scrollToSelected);
    const timer = setTimeout(scrollToSelected, 250);

    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
    };
  }, [selectedId]);

  useEffect(() => {
    if (contextMenu) {
      dismissTooltipImmediately();
    }
  }, [contextMenu, dismissTooltipImmediately]);

  useEffect(() => {
    if (!activeTooltip) return;

    const handlePointerDown = (event: PointerEvent | MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      const tooltipEl = document.getElementById("sidebar-hover-tooltip");
      if (tooltipEl && tooltipEl.contains(target)) {
        return;
      }
      dismissTooltipImmediately();
    };

    const dismissOnScrollOrResize = () => dismissTooltipImmediately();

    window.addEventListener("pointerdown", handlePointerDown, true);
    window.addEventListener("scroll", dismissOnScrollOrResize, true);
    window.addEventListener("resize", dismissOnScrollOrResize);

    return () => {
      window.removeEventListener("pointerdown", handlePointerDown, true);
      window.removeEventListener("scroll", dismissOnScrollOrResize, true);
      window.removeEventListener("resize", dismissOnScrollOrResize);
    };
  }, [activeTooltip, dismissTooltipImmediately]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (contextMenu) setContextMenu(undefined);
        dismissTooltipImmediately();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [contextMenu, dismissTooltipImmediately]);

  useEffect(() => {
    return () => {
      if (enterTimeoutRef.current) {
        clearTimeout(enterTimeoutRef.current);
      }
      if (leaveTimeoutRef.current) {
        clearTimeout(leaveTimeoutRef.current);
      }
    };
  }, []);

  const renderSessionList = (project: ProjectSessionGroup, isExpanded: boolean, emptyLabel = "暂无会话") => (
    <ProjectSessionList
      project={project}
      isExpanded={isExpanded}
      emptyLabel={emptyLabel}
      selectedId={selectedId}
      disabled={disabled}
      readCompletedSessions={readCompletedSessions}
      isSelectingSessionId={isSelectingSessionId}
      gitStatusBySessionId={gitStatusBySessionId}
      onSelect={(session) => {
        dismissTooltipImmediately();
        onSelect(session);
      }}
      onHoverSession={(session, projectDisplayName, element, statusLabel, statusTone) => {
        const isProjectless = project.projectless || session.projectless === true;
        handleTriggerEnter(
          {
            kind: "session",
            session,
            projectDisplayName: isProjectless ? undefined : projectDisplayName,
            projectless: isProjectless,
            statusLabel,
            statusTone,
          },
          element,
        );
      }}
      onLeaveSession={handleTriggerLeave}
      onDismissContextMenu={() => setContextMenu(undefined)}
      onOpenSessionContextMenu={(session, coords, element) => {
        dismissTooltipImmediately();
        setContextMenu({
          kind: "session",
          session,
          x: coords.x,
          y: coords.y,
          element,
        });
      }}
    />
  );

  const renderProject = (project: ProjectSessionGroup) => {
    const isExpanded = !collapsedProjects.has(project.key);
    return (
      <li key={project.key}>
        <section className={`workspace-group${project.projectless ? " workspace-group--recent" : ""}`} aria-label={project.displayName} data-project-id={project.projectId}>
          <div className={`workspace-title-row${project.projectless ? " workspace-title-row--recent" : ""}`}>
            {project.projectless ? (
              <button
                className="workspace-toggle workspace-toggle--recent"
                aria-expanded={isExpanded}
                onClick={() => {
                  dismissTooltipImmediately();
                  toggleProject(project.key);
                }}
                onMouseEnter={(e) =>
                  handleTriggerEnter(
                    {
                      kind: "project",
                      projectName: project.displayName,
                      workspaceRoot: project.workspaceRoot,
                      projectless: true,
                    },
                    e.currentTarget,
                  )
                }
                onMouseLeave={handleTriggerLeave}
                onFocus={(e) =>
                  handleTriggerEnter(
                    {
                      kind: "project",
                      projectName: project.displayName,
                      workspaceRoot: project.workspaceRoot,
                      projectless: true,
                    },
                    e.currentTarget,
                  )
                }
                onBlur={handleTriggerLeave}
              >
                <span className="workspace-name">{project.displayName}</span>
                <ChevronIcon open={isExpanded} />
              </button>
            ) : (
              <button
                className="workspace-toggle"
                aria-expanded={isExpanded}
                aria-haspopup={project.project ? "menu" : undefined}
                onClick={() => {
                  dismissTooltipImmediately();
                  toggleProject(project.key);
                }}
                onMouseEnter={(e) => {
                  const rowElement = (e.currentTarget.closest(".workspace-title-row") as HTMLElement) ?? e.currentTarget;
                  handleTriggerEnter(
                    {
                      kind: "project",
                      projectName: project.displayName,
                      workspaceRoot: project.workspaceRoot,
                      projectless: false,
                    },
                    rowElement,
                  );
                }}
                onMouseLeave={handleTriggerLeave}
                onFocus={(e) => {
                  const rowElement = (e.currentTarget.closest(".workspace-title-row") as HTMLElement) ?? e.currentTarget;
                  handleTriggerEnter(
                    {
                      kind: "project",
                      projectName: project.displayName,
                      workspaceRoot: project.workspaceRoot,
                      projectless: false,
                    },
                    rowElement,
                  );
                }}
                onBlur={handleTriggerLeave}
                onContextMenu={(event) => {
                  dismissTooltipImmediately();
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
                  dismissTooltipImmediately();
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
                  dismissTooltipImmediately();
                  setCollapsedProjects((current) => {
                    const next = new Set(current);
                    next.delete(project.key);
                    saveCollapsedProjects(next);
                    return next;
                  });
                  onCreateInProject(project.workspaceRoot);
                }}
                onMouseEnter={(e) => {
                  const rowElement = (e.currentTarget.closest(".workspace-title-row") as HTMLElement) ?? e.currentTarget;
                  handleTriggerEnter(
                    {
                      kind: "project",
                      projectName: project.displayName,
                      workspaceRoot: project.workspaceRoot,
                      projectless: false,
                    },
                    rowElement,
                  );
                }}
                onMouseLeave={handleTriggerLeave}
              >＋</button>
            )}
          </div>
          <div
            className={`workspace-collapse ${isExpanded ? "workspace-collapse--expanded" : ""}`}
            aria-hidden={!isExpanded}
            style={{ visibility: isExpanded ? "visible" : "hidden" }}
          >
            <div className="workspace-collapse-inner">
              {renderSessionList(project, isExpanded, project.sessions.length === 0 && projects.some((group) => group.key === project.key && group.sessions.length > 0) ? "会话已置顶" : "暂无会话")}
            </div>
          </div>
        </section>
      </li>
    );
  };

  // Dot CSS class derived from real RuntimePresentation tone
  const dotClass = `runtime-pulse-dot runtime-pulse-dot--${runtimePresentation.tone}${runtimePresentation.animated ? " runtime-pulse-dot--animated" : ""}`;

  return (
    <aside className="sidebar" aria-label="任务导航" inert={collapsed} aria-hidden={collapsed}>
      {navigationSlot && <div className="sidebar-top-bar">{navigationSlot}</div>}
      <div className="brand-row">
        <span className="brand-mark" aria-hidden="true">
          <EidosMark />
        </span>
        <div className="brand-titles">
          <span className="brand-name">Eidos</span>
          <span className="brand-badge">Desktop</span>
        </div>
        <Tooltip content="搜索所有会话" shortcut="⌘K">
          <button className="brand-search" aria-label="搜索所有会话" disabled={disabled} onClick={onSearch}><SearchIcon /></button>
        </Tooltip>
      </div>
      <PrimaryActionButton
        size="compact"
        label="新建会话"
        shortcut="⌘N"
        disabled={disabled}
        onClick={() => {
          dismissTooltipImmediately();
          onCreate();
        }}
      />
      <nav ref={navRef} aria-label="项目与最近" onScroll={dismissTooltipImmediately}>
        {onOpenAutomations && (
          <div className="sidebar-automation-section">
            <button
              type="button"
              className="sidebar-automation-entry"
              onClick={onOpenAutomations}
              aria-label="打开定时任务"
            >
              <AutomationsIcon className="sidebar-automation-icon" />
              <span className="sidebar-automation-label">定时任务</span>
            </button>
          </div>
        )}
        {sidebar.pinned.length > 0 && (
          <section className="sidebar-pinned" aria-label="置顶">
            <p className="nav-label">置顶</p>
            <ul className="workspace-list">
              {sidebar.pinned.map((entry) => entry.kind === "project"
                ? renderProject(entry.project)
                : <li key={`session:${entry.session.id}`} className="sidebar-pinned-session">
                    {renderSessionList({ ...entry.project, sessions: [entry.session] }, true)}
                  </li>)}
            </ul>
          </section>
        )}
        {(sidebar.projects.length > 0 || sidebar.pinned.length === 0) && (
          <>
            <p className="nav-label">项目</p>
            {projects.length === 0 ? (
              <p className="nav-empty">还没有任务，点击上方按键创建</p>
            ) : (
              <ul className="workspace-list">
                {sidebar.projects.map(renderProject)}
              </ul>
            )}
          </>
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
              key: "pin",
              label: pins.some((pin) => pin.kind === "session" && pin.id === contextMenu.session.id) ? "取消置顶" : "置顶",
              icon: <PinIcon />,
              disabled: pinningDisabled || !onSetPinned || contextMenu.session.id.startsWith("draft-")
                || pendingPinTargets?.has(sidebarTargetKey({ kind: "session", id: contextMenu.session.id })) === true,
              onClick: () => {
                const target: SidebarTarget = { kind: "session", id: contextMenu.session.id };
                requestPin(target, contextMenu.element);
              },
            },
            {
              key: "copy",
              label: "复制",
              icon: <CopyIcon />,
              children: [
                {
                  key: "copy-name",
                  label: "复制名称",
                  icon: <CopyIcon />,
                  onClick: () => {
                    const sessionName = contextMenu.session.title?.trim() || "新任务";
                    if (typeof navigator !== "undefined" && navigator?.clipboard?.writeText) {
                      void navigator.clipboard.writeText(sessionName);
                    }
                  },
                },
                {
                  key: "copy-workspace",
                  label: "复制项目目录",
                  icon: <CopyIcon />,
                  onClick: () => {
                    const workspacePath = contextMenu.session.project?.workspaceRoot || contextMenu.session.workspaceRoot || "";
                    if (typeof navigator !== "undefined" && navigator?.clipboard?.writeText) {
                      void navigator.clipboard.writeText(workspacePath);
                    }
                  },
                },
              ],
            },
            {
              key: "rename",
              label: "编辑标题",
              separator: true,
              icon: <EditIcon />,
              onClick: () => {
                setContextMenu(undefined);
                onRename(contextMenu.session);
              },
            },
            {
              key: "delete",
              label: "删除任务",
              icon: <TrashIcon />,
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
              key: "pin",
              label: pins.some((pin) => pin.kind === "project" && pin.id === contextMenu.project.id) ? "取消置顶" : "置顶",
              icon: <PinIcon />,
              disabled: pinningDisabled || !onSetPinned
                || pendingPinTargets?.has(sidebarTargetKey({ kind: "project", id: contextMenu.project.id })) === true,
              onClick: () => {
                const target: SidebarTarget = { kind: "project", id: contextMenu.project.id };
                requestPin(target, contextMenu.element);
              },
            },
            {
              key: "show-in-finder",
              label: "在 Finder 中显示",
              icon: <FinderIcon />,
              onClick: () => {
                setContextMenu(undefined);
                onShowInFinder?.(contextMenu.project);
              },
            },
            {
              key: "delete-project",
              label: "删除项目",
              icon: <TrashIcon />,
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
      <SidebarTooltip
        state={activeTooltip}
        sourceSession={activeTooltip?.kind === "session"
          ? sessions.find((session) => session.id === activeTooltip.session.forkedFrom?.sessionId)
          : undefined}
        onOpenSourceSession={(source) => {
          if (disabled) return;
          dismissTooltipImmediately();
          onSelect(source);
        }}
        onOpenAutomation={onOpenAutomation ? (id) => {
          if (disabled) return;
          dismissTooltipImmediately();
          onOpenAutomation(id);
        } : undefined}
        disabled={disabled}
        onMouseEnter={handleTooltipEnter}
        onMouseLeave={handleTooltipLeave}
      />
    </aside>
  );
}

export const DEFAULT_VISIBLE_SESSION_COUNT = 6;

interface ProjectSessionListProps {
  project: ProjectSessionGroup;
  isExpanded: boolean;
  emptyLabel?: string;
  selectedId: string | undefined;
  disabled: boolean;
  readCompletedSessions: ReadonlySet<string>;
  isSelectingSessionId?: string | undefined;
  gitStatusBySessionId: ReadonlyMap<string, SessionGitStatus>;
  onSelect: (session: Session) => void;
  onHoverSession?: (
    session: Session,
    projectDisplayName: string,
    element: HTMLElement,
    statusLabel?: string,
    statusTone?: "success" | "progress" | "error",
  ) => void;
  onLeaveSession?: () => void;
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
  emptyLabel = "暂无会话",
  selectedId,
  disabled,
  readCompletedSessions,
  isSelectingSessionId,
  gitStatusBySessionId,
  onSelect,
  onHoverSession,
  onLeaveSession,
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
    return <p className="session-empty">{emptyLabel}</p>;
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
            <li className="session-item" key={session.id} data-session-id={session.id}>
              <button
                className={isSelected ? "selected" : ""}
                aria-current={isSelected ? "page" : undefined}
                aria-busy={isLoading}
                aria-haspopup="menu"
                disabled={disabled}
                onClick={() => {
                  onLeaveSession?.();
                  onDismissContextMenu();
                  onSelect(session);
                }}
                onMouseEnter={(event) => {
                  onHoverSession?.(
                    session,
                    project.displayName,
                    event.currentTarget,
                    status?.label,
                    status?.tone,
                  );
                }}
                onMouseLeave={onLeaveSession}
                onFocus={(event) => {
                  onHoverSession?.(
                    session,
                    project.displayName,
                    event.currentTarget,
                    status?.label,
                    status?.tone,
                  );
                }}
                onBlur={onLeaveSession}
                onContextMenu={(event) => {
                  onLeaveSession?.();
                  event.preventDefault();
                  onOpenSessionContextMenu(
                    session,
                    { x: event.clientX, y: event.clientY },
                    event.currentTarget,
                  );
                }}
                onKeyDown={(event) => {
                  if ((event.shiftKey && event.key === "F10") || event.key === "ContextMenu") {
                    onLeaveSession?.();
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
                  <span className="session-title-row">
                    <span className="session-title">{session.title ?? "新任务"}</span>
                    {session.forkedFrom && <span className="session-fork-indicator" role="img" aria-label="分叉会话" title="分叉会话">
                      <ForkIcon />
                    </span>}
                    {session.automationOrigin && <span className="session-automation-indicator" role="img" aria-label="定时任务会话" title="定时任务会话">
                      <AutomationsIcon />
                    </span>}
                  </span>
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
          <ChevronDownIcon />
          展开全部
        </button>
      )}
    </>
  );
}

function ChevronDownIcon() {
  return (
    <svg className="session-expand-all-icon" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M4.5 6.5L8 10L11.5 6.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
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
