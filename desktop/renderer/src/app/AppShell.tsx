import { AgentList, AgentWorkspacePanel, useAgentState } from "../components/AgentPanel.js";
import type { AgentSummary } from "../../../shared/collaboration.generated.js";
import { PlanPanel } from "../components/PlanPanel.js";
import type { PlanDocument } from "../../../shared/planning.generated.js";
import type { SettingsCategory } from "../components/settings/settings-types.js";
import { InputContextProvider, InputDropZone } from "../components/InputContext.js";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import type { ApprovalMode, ApprovalRequest, ModelOption, Project, ResponseFeedbackValue, Run, Session, Item } from "../contracts.js";
import { SettingsPage } from "../components/settings/SettingsPage.js";
import { ExecutionFeed } from "../components/ExecutionFeed.js";
import { WelcomeMessage } from "../components/WelcomeMessage.js";
import { ChatSearch } from "../components/ChatSearch.js";
import type { SearchHit } from "../../../shared/search.js";
import { SessionSidebar } from "../components/SessionSidebar.js";
import { AutomationPage } from "../components/automations/AutomationPage.js";
import { Button } from "../components/Button.js";
import { DropdownMenu } from "../components/DropdownMenu.js";
import { EditIcon, TrashIcon } from "../components/MenuIcons.js";
import { ConfirmDialog } from "../components/settings/ConfirmDialog.js";
import { ForkConversationDialog } from "../components/ForkConversationDialog.js";
import { CreateBranchDialog } from "../components/CreateBranchDialog.js";
import { HandoffDialog } from "../components/HandoffDialog.js";
import { ProjectPicker } from "../components/ProjectPicker.js";
import { CreateProjectDialog } from "../components/CreateProjectDialog.js";
import { ComposerSlot } from "../components/ComposerSlot.js";
import { Composer } from "../components/Composer.js";
import { SessionCanvas } from "../components/SessionCanvas.js";
import { QueuedMessages } from "../components/QueuedMessages.js";
import { TextReviewPanel } from "../components/TextReviewPanel.js";
import { GitChangesPanel } from "../components/GitChangesPanel.js";
import { GitWorkflowControls } from "../components/GitWorkflowControls.js";
import {
  WorkspaceExplorer,
  type WorkspaceFileOpenRequest,
} from "../components/WorkspaceExplorer.js";
import { ArtifactProvider, artifactPath, type WorkspaceFilePosition } from "../components/ArtifactContext.js";
import { BrowserPanel, getBrowserTabTitle } from "../components/BrowserPanel.js";
import {
  WorkspaceDock,
  WorkspaceDockToggle,
  type WorkspaceTab,
  type WorkspaceToolKind,
} from "../components/WorkspaceDock.js";
import type { RuntimeLifecycleState } from "./useRuntimeLifecycle.js";
import { useSessionController } from "./useSessionController.js";
import { useSidebarPins } from "./useSidebarPins.js";
import { useRunController } from "./useRunController.js";
import { useApprovalController } from "./useApprovalController.js";
import { usePlanningState } from "./usePlanningState.js";
import { useModelController } from "./useModelController.js";
import { loadRunDefaults, saveRunDefaults } from "./run-defaults.js";
import { useResponseActionController } from "./useResponseActionController.js";
import { useContextUsageController } from "./useContextUsageController.js";
import { resolveSessionRun } from "./session-model-resolver.js";
import { useExtensionController } from "./useExtensionController.js";
import { useGitReviewController } from "./useGitReviewController.js";
import { applyNotification, applyQueuedRunChange, isQueuedMessage, userFacingError } from "../session-state.js";
import { IPC } from "../../../shared/ipc-channels.js";
import { NavigationControls } from "../components/NavigationControls.js";
import { useNavigationHistory } from "./useNavigationHistory.js";
import {
  collectOutputArtifacts,
  OutputContent,
  useCompleteSessionItems,
} from "../components/TurnResults.js";
import { ToastContainer, type ToastItem, type ToastType } from "../components/ToastContainer.js";

interface AppShellProps {
  runtime: RuntimeLifecycleState;
}

type CreateBranchMode = "local" | "worktree";

const SIDEBAR_OPEN_KEY = "eidos.sidebarOpen";

function loadSidebarOpen(): boolean {
  try {
    const raw = window.localStorage.getItem(SIDEBAR_OPEN_KEY);
    return raw !== null ? raw === "true" : true;
  } catch {
    return true;
  }
}

const DOCK_MIN_WIDTH = 20 * 16;
const MAIN_MIN_WIDTH = 16 * 16;
// Body width below which the side-by-side dock auto-collapses to protect the
// session column (composer project/mode/branch row) from horizontal squeezing.
const DOCK_AUTO_COLLAPSE_BODY_WIDTH = 40 * 16;
const EMPTY_MODELS: ModelOption[] = [];
const EMPTY_AGENTS: AgentSummary[] = [];

const TerminalPanel = lazy(() => import("../components/TerminalPanel.js").then((module) => ({
  default: module.TerminalPanel,
})));

/**
 * AppShell wires together domain controllers and renders the main layout.
 *
 * Responsibilities:
 * - Page layout and view switching
 * - Subscription to IPC notifications
 * - Keyboard shortcut dispatch from Main process menu
 * - Coordination between domain hooks
 *
 * Each domain hook owns its own state; AppShell only coordinates.
 */
export function AppShell({ runtime }: AppShellProps) {
  const { status: runtimeStatus, presentation: runtimePresentation, isStorageReady } = runtime;

  // Domain controllers
  const [sessionState, sessionActions] = useSessionController();
  const sidebarPins = useSidebarPins(isStorageReady && runtimeStatus.state === "ready");
  const activeSnapshot = sessionState.snapshot ?? sessionState.draft;
  const [runState, runActions] = useRunController(activeSnapshot, isStorageReady);
  const [approvalState, approvalActions] = useApprovalController();
  const [runDefaults, setRunDefaults] = useState(loadRunDefaults);
  const [modelState, modelActions] = useModelController();
  const [extensionState, extensionActions] = useExtensionController();
  const [gitReviewState, gitReviewActions] = useGitReviewController({
    ready: runtimeStatus.state === "ready" && isStorageReady,
    session: sessionState.snapshot?.session,
  });
  const [responseActionState, responseActionActions] = useResponseActionController(
    sessionState.snapshot?.session.id,
  );
  const latestRun = sessionState.snapshot?.runs.filter((run) => !run.inherited).at(-1);
  const [forkSelection, setForkSelection] = useState<{ session: Session; item: Item } | null>(null);
  const [forkTarget, setForkTarget] = useState<"same_workspace" | "new_worktree">("same_workspace");
  const sessionConfigRun = resolveSessionRun(sessionState.snapshot?.runs.filter((run) => !run.inherited) ?? []);
  const [approvalModes, setApprovalModes] = useState<Record<string, ApprovalMode>>({});
  const approvalSessionId = sessionState.draft && !sessionState.snapshot?.runs.length
    ? sessionState.draft.session.id
    : activeSnapshot?.session.id;
  const sessionApprovalMode = sessionConfigRun
    ? sessionConfigRun.approvalMode ?? "manual"
    : runDefaults.approvalMode ?? "manual";
  const approvalMode = (approvalSessionId ? approvalModes[approvalSessionId] : undefined)
    ?? sessionApprovalMode;
  const [workModes, setWorkModes] = useState<Record<string, "execute" | "plan">>({});
  const latestUserRun = sessionState.snapshot?.runs.filter((run) => !run.inherited && !run.automationOrigin).at(-1);
  const workMode = (approvalSessionId ? workModes[approvalSessionId] : undefined) ?? latestUserRun?.workMode ?? "execute";
  const contextRun = runState.activeRun ?? latestRun;
  const contextRunId = contextRun?.id;
  const [contextUsageState, contextUsageActions] = useContextUsageController({
    ready: runtimeStatus.state === "ready" && isStorageReady,
    sessionId: sessionState.snapshot?.session.id,
    modelId: contextRun?.modelId,
    runId: contextRunId,
  });
  const handleContextUsageNotification = contextUsageActions.handleNotification;

  const planning = usePlanningState(
    sessionState.snapshot?.session.id,
    runtimeStatus.state === "ready" && isStorageReady,
  );
  const agents = useAgentState(
    sessionState.snapshot?.session.id,
    runtimeStatus.state === "ready" && isStorageReady,
  );

  // UI-only state (not domain state)
  const searchSelection = useRef(0);
  const [searchMode, setSearchMode] = useState<"global" | "session">();
  const [searchTarget, setSearchTarget] = useState<{ itemId: string; query: string; occurrence: number; revision: number }>();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [automationsOpen, setAutomationsOpen] = useState(false);
  const [startingDraftId, setStartingDraftId] = useState<string>();
  const [automationDetailId, setAutomationDetailId] = useState<string | undefined>();
  const [settingsCategory, setSettingsCategory] = useState<SettingsCategory>("model");
  const [renamingSessionId, setRenamingSessionId] = useState<string | undefined>(undefined);
  const [titleDraft, setTitleDraft] = useState("");
  const [renameError, setRenameError] = useState<string | undefined>(undefined);
  const [sessionToDelete, setSessionToDelete] = useState<Session | undefined>(undefined);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | undefined>(undefined);
  const [projectToDelete, setProjectToDelete] = useState<Project | undefined>(undefined);
  const [projectDeleteBusy, setProjectDeleteBusy] = useState(false);
  const [projectDeleteError, setProjectDeleteError] = useState<string | undefined>(undefined);
  const [projectPickerOpen, setProjectPickerOpen] = useState(false);
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [createProjectFolder, setCreateProjectFolder] = useState<string | undefined>(undefined);
  const [createProjectBusy, setCreateProjectBusy] = useState(false);
  const [createProjectError, setCreateProjectError] = useState<string | undefined>(undefined);
  const [createBranchSessionId, setCreateBranchSessionId] = useState<string | undefined>(undefined);
  const [createBranchMode, setCreateBranchMode] = useState<CreateBranchMode>("worktree");
  const [handoffSessionId, setHandoffSessionId] = useState<string | undefined>(undefined);
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(loadSidebarOpen);
  const [browserRequest, setBrowserRequest] = useState<{ browserId: string; url: string; id: number }>();
  const [reviewRequest, setReviewRequest] = useState<{
    runId: string;
    path?: string;
    itemId?: string;
    requestId: number;
  }>();
  const [dockOpen, setDockOpen] = useState(false);
  const [dockRetained, setDockRetained] = useState(false);
  useEffect(() => {
    if (dockOpen) { setDockRetained(true); return; }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setDockRetained(false); return; }
    const timer = window.setTimeout(() => setDockRetained(false), 280);
    return () => window.clearTimeout(timer);
  }, [dockOpen]);
  const [dockExpanded, setDockExpanded] = useState(false);
  const [dockWidth, setDockWidth] = useState<number>();
  const wasNarrowRef = useRef(false);
  // Auto-collapse the side-by-side dock when the workspace body becomes too
  // narrow. Only triggers on the wide -> narrow transition so a user can still
  // manually reopen the dock while narrow without it snapping shut on every
  // resize event. Expanded (fullscreen) dock is left alone.
  useEffect(() => {
    const checkNarrow = () => {
      const measured = workspaceBodyRef.current?.getBoundingClientRect().width ?? 0;
      // jsdom / unlaid-out state reports 0; fall back to window width so tests
      // and first paint don't falsely count as narrow.
      const bodyWidth = measured > 0 ? measured : window.innerWidth;
      if (bodyWidth <= 0) return;
      const narrow = bodyWidth < DOCK_AUTO_COLLAPSE_BODY_WIDTH;
      const wasNarrow = wasNarrowRef.current;
      wasNarrowRef.current = narrow;
      if (narrow && !wasNarrow && dockOpen && !dockExpanded) {
        setDockOpen(false);
        setDockExpanded(false);
      }
    };
    checkNarrow();
    window.addEventListener("resize", checkNarrow);
    const body = workspaceBodyRef.current;
    const observer = typeof ResizeObserver !== "undefined" && body
      ? new ResizeObserver(checkNarrow)
      : undefined;
    if (body && observer) observer.observe(body);
    return () => {
      window.removeEventListener("resize", checkNarrow);
      observer?.disconnect();
    };
  }, [dockOpen, dockExpanded]);

  // Lock layout containers and window scroll so that no ancestor scrollIntoView or gesture can displace the desktop viewport.
  useEffect(() => {
    const layoutClasses = [
      "workbench",
      "workspace",
      "workspace-body",
      "workspace-main-column",
      "workspace-drawer",
      "workspace-dock",
      "workspace-dock__body",
    ];

    const enforceNoLayoutScroll = (event?: Event) => {
      // If user is currently typing in any input or textarea or inside composer, never reset
      if (
        document.activeElement instanceof HTMLInputElement ||
        document.activeElement instanceof HTMLTextAreaElement ||
        document.activeElement?.closest(".composer")
      ) {
        return;
      }

      const target = event?.target;
      // Never interfere with legitimate scrolling in content/input elements (textarea, input, composer, feed, code preview, file tree, terminal)
      if (target instanceof Element && (
        target.tagName === "TEXTAREA" ||
        target.tagName === "INPUT" ||
        target.closest(".composer") ||
        target.closest(".feed") ||
        target.closest(".workspace-preview-pane") ||
        target.closest(".workspace-tree-scroll") ||
        target.closest(".terminal-body")
      )) {
        return;
      }

      if (window.scrollY !== 0 || window.scrollX !== 0) {
        window.scrollTo(0, 0);
      }
      if (document.documentElement.scrollTop !== 0) document.documentElement.scrollTop = 0;
      if (document.documentElement.scrollLeft !== 0) document.documentElement.scrollLeft = 0;
      if (document.body && document.body.scrollTop !== 0) document.body.scrollTop = 0;
      if (document.body && document.body.scrollLeft !== 0) document.body.scrollLeft = 0;

      if (target instanceof HTMLElement) {
        if (target.id === "root" || layoutClasses.some((cls) => target.classList.contains(cls))) {
          if (target.scrollTop !== 0) target.scrollTop = 0;
          if (target.scrollLeft !== 0) target.scrollLeft = 0;
        }
      }
    };

    enforceNoLayoutScroll();
    window.addEventListener("scroll", enforceNoLayoutScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", enforceNoLayoutScroll);
    };
  }, []);
  const [environmentPopoverOpen, setEnvironmentPopoverOpen] = useState(false);
  const [openTabs, setOpenTabs] = useState<WorkspaceTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | undefined>(undefined);
  const [workflowOpenRequest, setWorkflowOpenRequest] = useState(0);
  const [explorerOpenRequest, setExplorerOpenRequest] = useState<WorkspaceFileOpenRequest | undefined>(undefined);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const workspaceBodyRef = useRef<HTMLDivElement>(null);
  const environmentPopoverRef = useRef<HTMLDetailsElement>(null);
  const dockResizeRef = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
  } | undefined>(undefined);
  const terminalSequenceRef = useRef(0);
  const browserSequenceRef = useRef(0);
  const fileOpenSequenceRef = useRef(0);
  const modelSessionInitializedRef = useRef<string | undefined>(undefined);
  const getDialogFallbackFocus = useCallback((): HTMLElement | null => {
    const composer = composerRef.current;
    return composer?.isConnected && !composer.disabled
      ? composer
      : workspaceRef.current;
  }, []);

  const topError = sessionState.error ?? sidebarPins.error ?? runState.error;
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    sessionActions.setError(undefined);
    sidebarPins.clearError();
    const activeSessionId = activeSnapshot?.session.id;
    if (activeSessionId) {
      runActions.clearError(activeSessionId);
    }
  }, [sessionActions, runActions, activeSnapshot?.session.id, sidebarPins.clearError]);

  const showToast = useCallback((message: string, type: ToastType = "info", duration?: number) => {
    const id = `${type}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setToasts((prev) => [
      ...prev.filter((t) => t.message !== message),
      {
        id,
        message,
        type,
        ...(duration !== undefined ? { duration } : {}),
      },
    ]);
  }, []);

  const previousTopErrorRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (topError && topError !== previousTopErrorRef.current) {
      const id = `error-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setToasts((prev) => [
        ...prev.filter((t) => t.type !== "error"),
        {
          id,
          message: topError,
          type: "error",
          duration: 5000,
        },
      ]);
    } else if (!topError && previousTopErrorRef.current) {
      setToasts((prev) => prev.filter((t) => t.type !== "error"));
    }
    previousTopErrorRef.current = topError;
  }, [topError]);

  const previousWarningRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const warningDesc = runtimePresentation.tone === "warning" ? runtimePresentation.description : undefined;
    if (warningDesc && warningDesc !== previousWarningRef.current) {
      const id = `runtime-warning-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setToasts((prev) => [
        ...prev.filter((t) => !t.id.startsWith("runtime-warning-")),
        {
          id,
          message: warningDesc,
          type: "warning",
          duration: 6000,
        },
      ]);
    } else if (!warningDesc && previousWarningRef.current) {
      setToasts((prev) => prev.filter((t) => !t.id.startsWith("runtime-warning-")));
    }
    previousWarningRef.current = warningDesc;
  }, [runtimePresentation.tone, runtimePresentation.description]);

  // -----------------------------------------------------------------------
  // Bootstrap: load sessions, model, approvals independently
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (runtimeStatus.state !== "ready" || runtimeStatus.storageHealth.state !== "ready") return;
    if (!sessionState.snapshot && !sessionState.draft && !sessionState.navigationSessionId) {
      sessionActions.startDraft(null);
    }
    void Promise.allSettled([
      sessionActions.loadProjects(),
      sessionActions.loadSessions(),
      modelActions.load(undefined, runDefaults.modelId),
      approvalActions.loadPending(),
    ]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runtimeStatus.state, runtimeStatus.state === "ready" ? runtimeStatus.storageHealth.state : null]);

  useEffect(() => {
    const snapshot = sessionState.snapshot;
    const draft = sessionState.draft;
    if (!modelState.list) return;
    if (draft && !snapshot?.runs.length) {
      if (modelSessionInitializedRef.current === draft.session.id) return;
      modelSessionInitializedRef.current = draft.session.id;
      modelActions.initialize(modelState.list, undefined, runDefaults.modelId);
      return;
    }
    if (!snapshot) return;
    if (modelSessionInitializedRef.current === snapshot.session.id) return;
    modelSessionInitializedRef.current = snapshot.session.id;
    modelActions.initialize(
      modelState.list,
      resolveSessionRun(snapshot.runs)?.modelId,
      runDefaults.modelId,
    );
  }, [
    modelState.list,
    sessionState.snapshot?.session.id,
    sessionState.snapshot?.runs.length,
    sessionState.draft?.session.id,
    runDefaults.modelId,
  ]);

  useEffect(() => {
    const sessionId = sessionState.snapshot?.session.id;
    if (!sessionId || runtimeStatus.state !== "ready" || !isStorageReady) return;
    void responseActionActions.load(sessionId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionState.snapshot?.session.id, runtimeStatus.state, isStorageReady]);

  useEffect(() => {
    setDockOpen(false);
    setDockExpanded(false);
    setDockWidth(undefined);
    setOpenTabs([]);
    setBrowserRequest(undefined);
    setActiveTabId(undefined);
    terminalSequenceRef.current = 0;
    browserSequenceRef.current = 0;
  }, [
    sessionState.snapshot?.session.id,
    sessionState.snapshot?.session.executionMode,
    sessionState.snapshot?.session.associatedWorktreeId,
  ]);

  // -----------------------------------------------------------------------
  // Runtime notifications
  // -----------------------------------------------------------------------
  useEffect(() => {
    const unsubNotifications = window.eidosRuntime?.onNotification?.((notification) => {
      handleContextUsageNotification(notification);
      gitReviewActions.handleNotification(notification);
      if (notification.method === "sidebar/pinsChanged") {
        sidebarPins.handleChanged(notification.params.eventId);
      } else if (notification.method === "run/queueChanged") {
        void sessionActions.refreshCompletedSession(notification.params.change.sessionId);
      } else if (notification.method === "session/titleUpdated") {
        sessionActions.handleTitleNotification(notification.params);
      } else if (
        notification.method === "run/started"
        || notification.method === "run/updated"
        || notification.method === "run/completed"
      ) {
        const { run } = notification.params;
        sessionActions.handleRunNotification(run);
        if (notification.method === "run/completed") {
          approvalActions.clearApprovalsForRun(run.id);
          void sessionActions.refreshCompletedSession(notification.params.sessionId);
        }
      } else if (
        notification.method === "approval/resolved"
        || notification.method === "approval/canceled"
      ) {
        approvalActions.removeApproval(notification.params.approvalId);
      }
      sessionActions.setSnapshot((prev) => applyNotification(prev, notification));
    });

    const unsubApprovals = window.eidosRuntime?.onApprovalRequest?.((request) => {
      approvalActions.addApproval(request);
    });

    return () => {
      unsubNotifications?.();
      unsubApprovals?.();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleContextUsageNotification, gitReviewActions.handleNotification]);

  // -----------------------------------------------------------------------
  // Keyboard shortcuts from Main process menu
  // -----------------------------------------------------------------------
  const hasBlockingModal =
    Boolean(sessionToDelete) ||
    deleteBusy ||
    Boolean(projectToDelete) ||
    projectDeleteBusy ||
    Boolean(createBranchSessionId) ||
    Boolean(handoffSessionId) ||
    projectPickerOpen ||
    createProjectOpen ||
    sessionState.pending.branchSessionId !== undefined ||
    sessionState.pending.creatingBranchSessionId !== undefined ||
    sessionState.pending.handoffSessionId !== undefined ||
    sessionState.pending.creatingSession === true;

  useEffect(() => {
    const unsubNewTask = window.eidosRuntime?.onShortcut?.(IPC.APP_NEW_TASK, () => {
      if (hasBlockingModal || settingsOpen) return;
      handleCreateSession();
    });
    const unsubOpenWorkspace = window.eidosRuntime?.onShortcut?.(IPC.APP_OPEN_WORKSPACE, () => {
      if (hasBlockingModal || settingsOpen) return;
      setProjectPickerOpen(true);
    });
    return () => {
      unsubNewTask?.();
      unsubOpenWorkspace?.();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasBlockingModal, settingsOpen]);

  // -----------------------------------------------------------------------
  // Extension refresh when settings open
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!settingsOpen || runtimeStatus.state !== "ready" || !isStorageReady) return;
    void extensionActions.load();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingsOpen, runtimeStatus.state, isStorageReady]);

  const toggleSidebar = useCallback(() => {
    setSidebarOpen((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(SIDEBAR_OPEN_KEY, String(next));
      } catch {}
      return next;
    });
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      const isCmdOrCtrl = event.metaKey || event.ctrlKey;
      if (isCmdOrCtrl && event.key.toLowerCase() === "k" && !(event.target as Element)?.closest(".terminal-panel")) { event.preventDefault(); setSearchMode("global"); }
      if (isCmdOrCtrl && event.key.toLowerCase() === "f" && !settingsOpen && !automationsOpen && !(event.target as Element)?.closest(".terminal-panel,.browser-panel,.workspace-preview")) {
        event.preventDefault(); setSearchMode("session");
        const input = document.querySelector<HTMLInputElement>(".workspace-main-column > .find-bar input"); input?.focus(); input?.select();
      }
      if (isCmdOrCtrl && (event.key === "b" || event.key === "B")) {
        event.preventDefault();
        if (!settingsOpen && !automationsOpen) toggleSidebar();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [toggleSidebar, settingsOpen, automationsOpen]);

  // -----------------------------------------------------------------------
  // Session Selection & Creation with Model re-eval
  const handleNavigateToSession = useCallback((target: string) => {
    setAutomationsOpen(false);
    if (target === "draft") {
      sessionActions.startDraft();
      return;
    }
    const targetSession = sessionState.sessions.find((s) => s.id === target);
    if (targetSession) {
      void sessionActions.selectSession(targetSession);
    } else {
      void sessionActions.selectSession({
        id: target,
        workspaceRoot: "",
        executionMode: "local",
        taskStatus: "in_progress",
        createdAt: 0,
        updatedAt: 0,
      });
    }
  }, [sessionState.sessions, sessionActions]);

  const [navHistoryState, navHistoryActions] = useNavigationHistory(handleNavigateToSession);

  // Initialize history with initial session or draft once
  useEffect(() => {
    if (navHistoryState.history.length === 0) {
      if (sessionState.snapshot?.session.id) {
        navHistoryActions.init(sessionState.snapshot.session.id);
      } else if (sessionState.draft) {
        navHistoryActions.init("draft");
      }
    }
  }, [sessionState.snapshot?.session.id, sessionState.draft, navHistoryState.history.length, navHistoryActions]);

  const canGoBack = settingsOpen || automationsOpen || navHistoryState.canGoBack;
  const canGoForward = !settingsOpen && !automationsOpen && navHistoryState.canGoForward;
  const handleGoBack = useCallback(() => {
    if (settingsOpen) {
      setSettingsOpen(false);
    } else if (automationsOpen) {
      setAutomationsOpen(false);
    } else {
      void navHistoryActions.goBack();
    }
  }, [settingsOpen, automationsOpen, navHistoryActions]);
  const handleGoForward = useCallback(() => {
    if (!settingsOpen && !automationsOpen) {
      void navHistoryActions.goForward();
    }
  }, [settingsOpen, automationsOpen, navHistoryActions]);

  function clearSearchTarget() { searchSelection.current += 1; setSearchTarget(undefined); }

  async function handleSearchSelect(hit: SearchHit, query: string): Promise<void> {
    const selection = ++searchSelection.current;
    const session = sessionState.sessions.find((candidate) => candidate.id === hit.sessionId) ?? (await window.eidosRuntime.readSession(hit.sessionId)).session;
    if (selection !== searchSelection.current) return;
    setSettingsOpen(false); setAutomationsOpen(false);
    navHistoryActions.navigateTo(session.id);
    const loaded = await sessionActions.selectSession(session, hit.itemId ?? undefined);
    if (selection !== searchSelection.current) return;
    if (loaded && hit.itemId) setSearchTarget({ itemId: hit.itemId, query, occurrence: hit.occurrence ?? 0, revision: Date.now() });
    if (searchMode === "global") setSearchMode(undefined);
  }

  async function handleSelectSession(session: Session) {
    setSearchTarget(undefined); setSearchMode(undefined);
    setAutomationsOpen(false);
    navHistoryActions.navigateTo(session.id);
    return sessionActions.selectSession(session);
  }

  function handleCreateSession(project?: Project | null): void {
    setAutomationsOpen(false);
    navHistoryActions.navigateTo("draft");
    sessionActions.startDraft(project);
  }

  async function handleSelectProject(): Promise<void> {
    setCreateProjectError(undefined);
    setProjectPickerOpen(true);
  }

  function handleSelectProjectFromPicker(project: Project): void {
    setProjectPickerOpen(false);
    handleCreateSession(project);
  }

  function handleOpenCreateProject(): void {
    setProjectPickerOpen(false);
    setCreateProjectError(undefined);
    setCreateProjectFolder(undefined);
    setCreateProjectOpen(true);
  }

  async function handleSelectProjectFolder(): Promise<void> {
    try {
      const workspace = await window.eidosRuntime.selectWorkspace();
      if (workspace) {
        setCreateProjectFolder(workspace);
        setCreateProjectError(undefined);
      }
    } catch (cause) {
      setCreateProjectError(userFacingError(cause));
    }
  }

  async function handleCreateProject(name: string | undefined, workspaceRoot: string): Promise<void> {
    setCreateProjectBusy(true);
    setCreateProjectError(undefined);
    const project = await sessionActions.createProject(name, workspaceRoot);
    setCreateProjectBusy(false);
    if (!project) {
      setCreateProjectError(sessionState.error ?? "项目创建失败，请重试。");
      return;
    }
    setCreateProjectOpen(false);
    handleCreateSession(project);
  }

  function handleCreateInProject(workspaceRoot: string): void {
    const project = sessionState.projects.find((item) => item.workspaceRoot === workspaceRoot);
    handleCreateSession(project ?? null);
  }

  async function confirmCreateBranch(branch: string): Promise<void> {
    if (!createBranchSessionId) return;
    const result = createBranchMode === "local"
      ? await sessionActions.createLocalBranch(createBranchSessionId, branch)
      : await sessionActions.createSessionBranch(createBranchSessionId, branch);
    if (result) {
      setCreateBranchSessionId(undefined);
      setCreateBranchMode("worktree");
      gitReviewActions.refresh();
    }
  }

  function openCreateBranch(sessionId: string, mode: CreateBranchMode): void {
    sessionActions.setError(undefined);
    setCreateBranchMode(mode);
    setCreateBranchSessionId(sessionId);
  }

  async function switchLocalBranch(branch: string): Promise<void> {
    const sessionId = sessionState.snapshot?.session.id;
    if (!sessionId || !branch || branch === sessionBranch) return;
    const result = await sessionActions.switchLocalBranch(sessionId, branch);
    if (result) gitReviewActions.refresh();
  }

  async function confirmHandoff(target: "local" | "worktree", branch?: string): Promise<void> {
    if (!handoffSessionId) return;
    const current = sessionState.snapshot;
    if (target === "local" && current?.session.executionMode === "local") {
      if (!branch || branch === sessionBranch) return;
      const switched = await sessionActions.switchLocalBranch(handoffSessionId, branch);
      if (switched) {
        setHandoffSessionId(undefined);
        gitReviewActions.refresh();
      }
      return;
    }
    const loaded = await sessionActions.handoffSession(handoffSessionId, target);
    if (loaded) setHandoffSessionId(undefined);
  }

  function requestExecutionModeChange(target: "local" | "worktree"): void {
    const current = sessionState.snapshot ?? sessionState.draft;
    if (!current || current.session.executionMode === target) return;
    if (sessionState.draft && !sessionState.snapshot) {
      sessionActions.updateDraftExecutionMode(target);
      return;
    }
    setHandoffSessionId(current.session.id);
  }

  // -----------------------------------------------------------------------
  // Rename flow
  // -----------------------------------------------------------------------
  async function beginRename(session: Session): Promise<void> {
    let targetSnapshot = sessionState.snapshot;
    if (targetSnapshot?.session.id !== session.id) {
      targetSnapshot = await handleSelectSession(session);
    }
    if (!targetSnapshot || targetSnapshot.session.id !== session.id) {
      return;
    }
    setTitleDraft(targetSnapshot.session.title ?? "新会话");
    setRenameError(undefined);
    setRenamingSessionId(session.id);
  }

  async function submitRename(): Promise<void> {
    const sid = sessionState.snapshot?.session.id;
    if (!sid || renamingSessionId !== sid || !titleDraft.trim()) return;
    setRenameError(undefined);
    try {
      await sessionActions.renameSession(sid, titleDraft.trim());
      setRenamingSessionId(undefined);
    } catch (cause) {
      setRenameError(userFacingError(cause));
    }
  }

  // -----------------------------------------------------------------------
  // Delete flow
  // -----------------------------------------------------------------------
  function requestDeleteSession(session: Session): void {
    setDeleteError(undefined);
    setSessionToDelete(session);
  }

  async function confirmDelete(): Promise<void> {
    if (!sessionToDelete) return;
    setDeleteBusy(true);
    setDeleteError(undefined);
    const result = await sessionActions.deleteSession(sessionToDelete);
    setDeleteBusy(false);
    if (result.confirmed) {
      navHistoryActions.removeSession(sessionToDelete.id);
      setSessionToDelete(undefined);
    } else {
      setDeleteError(result.error);
    }
  }

  function requestDeleteProject(project: Project): void {
    setProjectDeleteError(undefined);
    setProjectToDelete(project);
  }

  async function confirmDeleteProject(): Promise<void> {
    if (!projectToDelete) return;
    setProjectDeleteBusy(true);
    setProjectDeleteError(undefined);
    const result = await sessionActions.deleteProject(projectToDelete);
    setProjectDeleteBusy(false);
    if (result.confirmed) {
      setProjectToDelete(undefined);
    } else {
      setProjectDeleteError(result.error);
    }
  }

  // -----------------------------------------------------------------------
  // Run submission and revision
  // -----------------------------------------------------------------------
  async function handleSubmit(): Promise<void> {
    if (!modelState.selectedModelId) return;
    const draftSnapshot = sessionState.draft;
    if (draftSnapshot && !sessionState.snapshot) {
      const draftInput = runState.input;
      const draftReferences = runState.references;
      if (!draftInput.trim() && !draftReferences.length) return;
      const workspaceNameHint = draftInput.trim()
        ? Array.from(draftInput.trim().split(/\r?\n/, 1)[0] ?? "").slice(0, 512).join("")
        : "附件任务";
      setStartingDraftId(draftSnapshot.session.id);
      try {
        const materialized = await sessionActions.materializeDraft(workspaceNameHint);
        if (!materialized) return;
        navHistoryActions.replaceCurrent(materialized.session.id);
        const started = await runActions.submitInput({
          snapshot: materialized,
          selectedModelId: modelState.selectedModelId,
          reasoningSelection: modelState.reasoningSelection,
          approvalMode,
          planning: { workMode },
          isStorageReady,
          inputOverride: draftInput,
          referencesOverride: draftReferences,
          onRunProjected: (sessionId, run) => {
            sessionActions.projectRun(sessionId, run);
            if (draftSnapshot.runs.length === 0) {
              setRunDefaults(saveRunDefaults(run));
            }
          },
        });
        if (started) {
          runActions.clearDraftIfUnchanged(draftSnapshot.session.id, draftInput, draftReferences);
          sessionActions.discardDraft();
        } else {
          const rolledBack = await sessionActions.rollbackMaterializedSession(materialized.session, draftSnapshot);
          if (!rolledBack) sessionActions.setError("Run 启动结果不明确，已保留 Session 供检查。");
        }
      } finally {
        setStartingDraftId((current) => current === draftSnapshot.session.id ? undefined : current);
      }
      return;
    }
    if (!sessionState.snapshot) return;
    await runActions.submitInput({
      snapshot: sessionState.snapshot,
      selectedModelId: modelState.selectedModelId,
      reasoningSelection: modelState.reasoningSelection,
      approvalMode,
      planning: { workMode },
      isStorageReady,
      onRunProjected: sessionActions.projectRun,
    });
  }

  async function submitPlan(plan: PlanDocument, feedback?: string): Promise<boolean> {
    if (!sessionState.snapshot || !modelState.selectedModelId) return false;
    const mode = feedback === undefined ? "execute" : "plan";
    const started = await runActions.submitInput({
      snapshot: sessionState.snapshot,
      selectedModelId: modelState.selectedModelId,
      reasoningSelection: modelState.reasoningSelection,
      approvalMode,
      planning: { workMode: mode, planId: plan.id, planRevision: plan.revision },
      inputOverride: feedback === undefined ? "按已确认的计划执行。" : `请根据以下意见修改计划：\n${feedback}`,
      isStorageReady,
      onRunProjected: sessionActions.projectRun,
    });
    if (started) setWorkModes((previous) => ({ ...previous, [plan.sessionId]: mode }));
    return started;
  }

  async function handleReviewFeedback(feedback: string): Promise<void> {
    if (!sessionState.snapshot || !modelState.selectedModelId) return;
    await runActions.submitInput({
      snapshot: sessionState.snapshot,
      selectedModelId: modelState.selectedModelId,
      reasoningSelection: modelState.reasoningSelection,
      approvalMode,
      isStorageReady,
      inputOverride: feedback,
      onRunProjected: sessionActions.projectRun,
    });
  }

  const reviseLatestRun = useCallback(async (run: Run, userInput?: string, references?: string[]): Promise<boolean> => {
    const snapshot = sessionState.snapshot;
    if (!snapshot || run.sessionId !== snapshot.session.id) return false;
    return runActions.reviseRun({
      snapshot,
      sourceRunId: run.id,
      approvalMode,
      ...(userInput !== undefined ? { userInput } : {}),
      ...(references !== undefined ? { references } : {}),
      isStorageReady,
      onRunProjected: sessionActions.projectRun,
      onRevisionProjected: (revision) => {
        responseActionActions.projectRevision(snapshot.session.id, revision);
      },
      onRefreshSession: sessionActions.refreshCompletedSession,
    });
  }, [approvalMode, isStorageReady, responseActionActions, runActions, sessionActions, sessionState.snapshot]);

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------
  const { composerMode, activeRun, input } = runState;
  const { snapshot } = sessionState;
  const currentSnapshot = snapshot ?? sessionState.draft;
  const isDraft = Boolean(!snapshot && sessionState.draft);
  const showWelcome = Boolean(currentSnapshot
    && (isDraft || (currentSnapshot.session.taskStatus === "new"
      && currentSnapshot.runs.length === 0 && currentSnapshot.items.length === 0))
    && !(startingDraftId && sessionState.draft?.session.id === startingDraftId));
  // 上下文栏只服务于首次 Run 之前：项目、执行方式和分支一旦被 Run 固定就不再可选。
  // runs 是本地即时事实，taskStatus 来自权威快照；两者都指向"尚未开始"才展示。
  const showSessionSetup = Boolean(currentSnapshot
    && currentSnapshot.session.taskStatus === "new"
    && currentSnapshot.runs.length === 0);
  const completeSessionItems = useCompleteSessionItems(
    currentSnapshot && !isDraft ? currentSnapshot.session.id : undefined,
    currentSnapshot?.items ?? [],
    currentSnapshot?.previousItemId,
  );
  const outputArtifacts = collectOutputArtifacts(completeSessionItems.items);
  const { approvals, respondingApprovalIds, respondingKindByApprovalId, errorsByApprovalId } = approvalState;
  const currentSessionId = currentSnapshot?.session.id;
  const feedModels = modelState.list?.models ?? EMPTY_MODELS;
  const feedAgents = agents.state?.agents ?? EMPTY_AGENTS;
  const feedApprovals = useMemo(
    () => (currentSessionId ? approvals.filter((a) => a.sessionId === currentSessionId) : []),
    [approvals, currentSessionId],
  );

  const handleFeedRetryLoadPending = useCallback(() => {
    void approvalActions.loadPending();
  }, [approvalActions]);

  const handleFeedApprove = useCallback((request: ApprovalRequest) => {
    void approvalActions.approve(request);
  }, [approvalActions]);

  const handleFeedReject = useCallback((request: ApprovalRequest) => {
    void approvalActions.reject(request);
  }, [approvalActions]);

  const handleFeedFeedback = useCallback(async (itemId: string, feedback: ResponseFeedbackValue | null) => {
    if (currentSessionId) {
      await responseActionActions.setFeedback(currentSessionId, itemId, feedback);
    }
  }, [currentSessionId, responseActionActions]);

  const handleFeedRegenerate = useCallback(async (run: Run) => {
    await reviseLatestRun(run);
  }, [reviseLatestRun]);

  const createConversationFork = useCallback(async (source: Session, item: Item, kind: "same_workspace" | "new_worktree") => {
    const loaded = await sessionActions.forkSession(source.id, item.id,
      kind === "same_workspace" ? { kind } : { kind, state: "current_workspace" });
    if (loaded) {
      setForkSelection(null);
      const inheritedCount = loaded.session.forkedFrom?.itemCount ?? 0;
      showToast(`本会话从历史回答分叉，已继承 ${inheritedCount} 条记录。`, "info", 3500);
      requestAnimationFrame(() => composerRef.current?.focus());
    }
  }, [sessionActions.forkSession, showToast]);
  const handleFeedFork = useCallback((item: Item) => {
    const source = sessionState.snapshot?.session;
    if (!source || sessionState.pending.creatingSession) return;
    if (!source.project?.gitAvailable) {
      void createConversationFork(source, item, "same_workspace");
      return;
    }
    setForkTarget(source.executionMode === "worktree" ? "new_worktree" : "same_workspace");
    setForkSelection({ session: source, item });
  }, [sessionState.snapshot?.session, sessionState.pending.creatingSession, createConversationFork]);

  const handleFeedEditResend = useCallback((run: Run, editedInput?: string, references?: string[]) => {
    return reviseLatestRun(run, editedInput, references);
  }, [reviseLatestRun]);

  const agentApprovalCounts = Object.fromEntries((agents.state?.agents ?? []).map((agent) => [
    agent.sessionId,
    approvals.filter((request) => request.sessionId === agent.sessionId).length,
  ]));
  const pendingAgentApprovals = Object.values(agentApprovalCounts).reduce((total, count) => total + count, 0);
  const sessionWorktree = currentSnapshot?.session.worktree;
  const sessionIsLocal = currentSnapshot?.session.executionMode === "local"
    || (currentSnapshot?.session.executionMode === undefined && sessionWorktree === undefined);
  const selectedProject = currentSnapshot?.session.project?.id
    ? sessionState.projects.find((project) => project.id === currentSnapshot.session.project?.id)
    : undefined;
  const sessionProject = selectedProject ?? currentSnapshot?.session.project;
  // Draft 状态下也允许根据关联项目类型计算 hasProject/hasGit，
  // 使环境信息在新会话有项目时也能显示。
  const sessionHasProject = Boolean(currentSnapshot && currentSnapshot.session.projectless !== true && sessionProject);
  const sessionHasGit = sessionProject?.gitAvailable === true;
  const sessionBranch = gitReviewState.status?.branch ?? sessionWorktree?.branch ?? null;
  const handoffBusy = Boolean(snapshot && sessionState.pending.handoffSessionId === snapshot.session.id);
  const restoreBusy = Boolean(snapshot && sessionState.pending.restoringWorktreeSessionId === snapshot.session.id);
  const worktreeRestoreRequired = currentSnapshot?.session.executionMode === "worktree"
    && currentSnapshot.session.worktreeRestoreAvailable === true;
  const executionKey = currentSnapshot
    ? [
        currentSnapshot.session.id,
        currentSnapshot.session.executionMode ?? "local",
        currentSnapshot.session.associatedWorktreeId ?? "workspace",
        currentSnapshot.session.worktree?.state ?? "available",
        currentSnapshot.session.worktree?.worktreeRoot ?? currentSnapshot.session.workspaceRoot,
      ].join(":")
    : "empty";
  const availableTools = useMemo<WorkspaceToolKind[]>(() => currentSnapshot
    ? [
        ...(agents.state?.agents?.length ? ["agent" as const] : []),
        ...(sessionHasGit ? ["review" as const] : []),
        ...(sessionHasProject ? ["terminal", "files"] as const : []),
        "browser",
      ]
    : [], [currentSnapshot, agents.state?.agents?.length, sessionHasGit, sessionHasProject]);

  useEffect(() => {
    setEnvironmentPopoverOpen(false);

    const closeOnPointerDown = (event: PointerEvent) => {
      const details = environmentPopoverRef.current;
      if (!details) return;
      if (event.target instanceof Node && details.contains(event.target)) return;
      details.open = false;
      setEnvironmentPopoverOpen(false);
    };
    const closeOnKeyDown = (event: KeyboardEvent) => {
      const details = environmentPopoverRef.current;
      if (event.key === "Escape" && details) {
        details.open = false;
        setEnvironmentPopoverOpen(false);
      }
    };

    window.addEventListener("pointerdown", closeOnPointerDown);
    window.addEventListener("keydown", closeOnKeyDown);
    return () => {
      window.removeEventListener("pointerdown", closeOnPointerDown);
      window.removeEventListener("keydown", closeOnKeyDown);
    };
  }, [executionKey, sessionHasProject]);

  useEffect(() => {
    setReviewRequest(undefined);
    setWorkflowOpenRequest(0);
  }, [currentSnapshot?.session.id]);

  function getDockResizeBounds(): { min: number; max: number } {
    const bodyWidth = workspaceBodyRef.current?.getBoundingClientRect().width || window.innerWidth;
    const availableWidth = Math.max(MAIN_MIN_WIDTH, bodyWidth - MAIN_MIN_WIDTH);
    const min = Math.min(DOCK_MIN_WIDTH, availableWidth);
    const max = Math.max(min, Math.min(availableWidth, Math.round(bodyWidth * 0.62)));
    return { min, max };
  }

  function clampDockWidth(width: number): number {
    const { min, max } = getDockResizeBounds();
    return Math.min(max, Math.max(min, Math.round(width)));
  }

  function getCurrentDockWidth(): number {
    if (dockWidth !== undefined) return dockWidth;
    const dock = workspaceBodyRef.current?.querySelector<HTMLElement>(".workspace-dock");
    const measured = dock?.getBoundingClientRect().width ?? 0;
    return clampDockWidth(measured || DOCK_MIN_WIDTH);
  }

  function adjustDockWidth(delta: number): void {
    setDockWidth(clampDockWidth(getCurrentDockWidth() + delta));
  }

  function handleDockResizePointerDown(event: ReactPointerEvent<HTMLDivElement>): void {
    if (dockExpanded) return;
    workspaceBodyRef.current?.classList.add("workspace-body--resizing");
    dockResizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: getCurrentDockWidth(),
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function handleDockResizePointerMove(event: ReactPointerEvent<HTMLDivElement>): void {
    const drag = dockResizeRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setDockWidth(clampDockWidth(drag.startWidth - (event.clientX - drag.startX)));
  }

  function finishDockResize(event: ReactPointerEvent<HTMLDivElement>): void {
    if (dockResizeRef.current?.pointerId === event.pointerId) {
      dockResizeRef.current = undefined;
      workspaceBodyRef.current?.classList.remove("workspace-body--resizing");
    }
  }

  function handleDockResizeKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      adjustDockWidth(16);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      adjustDockWidth(-16);
    } else if (event.key === "Home") {
      event.preventDefault();
      setDockWidth(getDockResizeBounds().min);
    } else if (event.key === "End") {
      event.preventDefault();
      setDockWidth(getDockResizeBounds().max);
    }
  }

  const openTool = useCallback((tool: WorkspaceToolKind): string | undefined => {
    if (!availableTools.includes(tool)) return;
    if (environmentPopoverRef.current) environmentPopoverRef.current.open = false;
    setEnvironmentPopoverOpen(false);
    const existing = tool === "terminal" || tool === "browser"
      ? undefined
      : openTabs.find((tab) => tab.id === tool || (tab.kind === tool && !tab.id.startsWith("agent-")));
    let tab = existing;
    if (!tab && tool === "terminal") {
      const index = ++terminalSequenceRef.current;
      tab = { id: `terminal-${index}`, kind: tool, title: `终端 ${index}` };
    } else if (!tab && tool === "browser") {
      const index = ++browserSequenceRef.current;
      tab = { id: `browser-${index}`, kind: tool, title: index === 1 ? "新标签页" : `新标签页 ${index}` };
    } else if (!tab) {
      tab = { id: tool, kind: tool };
    }
    if (!existing) setOpenTabs((current) => current.some((entry) => entry.id === tab.id) ? current : [...current, tab]);
    setActiveTabId(tab.id);
    setDockOpen(true);
    return tab.id;
  }, [availableTools, openTabs]);

  const openAgentList = useCallback((): void => {
    if (!currentSnapshot) return;
    const existing = openTabs.find((tab) => tab.id === "agent");
    if (!existing) {
      setOpenTabs((current) => [...current, { id: "agent", kind: "agent", title: "智能体" }]);
    }
    setActiveTabId("agent");
    setDockOpen(true);
  }, [currentSnapshot, openTabs]);

  const openAgent = useCallback((agent: AgentSummary): void => {
    if (!currentSnapshot || !agents.state?.agents?.some((entry) => entry.id === agent.id)) return;
    if (environmentPopoverRef.current) environmentPopoverRef.current.open = false;
    setEnvironmentPopoverOpen(false);
    const id = `agent-${agent.id}`;
    setOpenTabs((tabs) => tabs.some((tab) => tab.id === id) ? tabs : [...tabs, { id, kind: "agent", title: agent.taskName }]);
    setActiveTabId(id);
    setDockOpen(true);
  }, [agents.state?.agents, currentSnapshot]);

  const handleOpenSubagent = useCallback((target?: { id?: string | undefined; taskName?: string | undefined }): void => {
    if (!currentSnapshot) return;
    if (environmentPopoverRef.current) environmentPopoverRef.current.open = false;
    setEnvironmentPopoverOpen(false);

    const allAgents = agents.state?.agents ?? [];
    const matched = target?.id
      ? allAgents.find((entry) => entry.id === target.id)
      : target?.taskName
        ? allAgents.find((entry) => entry.taskName === target.taskName)
        : undefined;

    if (matched) {
      openAgent(matched);
    } else if (target?.id) {
      const id = `agent-${target.id}`;
      const title = target.taskName || "智能体";
      setOpenTabs((tabs) => tabs.some((tab) => tab.id === id) ? tabs : [...tabs, { id, kind: "agent", title }]);
      setActiveTabId(id);
      setDockOpen(true);
    } else {
      openAgentList();
    }
  }, [agents.state?.agents, currentSnapshot, openAgent, openAgentList]);

  function handleOpenBrowser(url: string): void {
    const browserId = openTool("browser");
    if (!browserId) return;
    setBrowserRequest({ browserId, url, id: ++fileOpenSequenceRef.current });
  }

  function handleOpenReview(request: { runId: string; path?: string; itemId?: string }): void {
    if (!currentSnapshot) return;
    setOpenTabs((tabs) => tabs.some((tab) => tab.kind === "text-review") ? tabs : [...tabs, { id: "text-review", kind: "text-review" }]);
    setActiveTabId("text-review");
    setDockOpen(true);
    setReviewRequest({ ...request, requestId: ++fileOpenSequenceRef.current });
  }

  const handleOpenPlan = useCallback((): void => {
    if (!currentSnapshot) return;
    if (environmentPopoverRef.current) environmentPopoverRef.current.open = false;
    setEnvironmentPopoverOpen(false);
    setOpenTabs((tabs) => tabs.some((tab) => tab.kind === "plan") ? tabs : [...tabs, { id: "plan", kind: "plan", title: "计划" }]);
    setActiveTabId("plan");
    setDockOpen(true);
  }, [currentSnapshot]);

  async function handleOpenExternal(path: string): Promise<void> {
    if (!currentSnapshot) return;
    const root = currentSnapshot.session.worktree?.worktreeRoot ?? currentSnapshot.session.workspaceRoot;
    const resolved = artifactPath(path, root, undefined, true);
    if (!resolved) throw new Error("文件不在当前工作区内。");
    await window.eidosRuntime.openWorkspacePathInEditor(currentSnapshot.session.id, resolved);
  }

  async function handleShowInFinder(path: string): Promise<void> {
    if (!currentSnapshot) return;
    const root = currentSnapshot.session.worktree?.worktreeRoot ?? currentSnapshot.session.workspaceRoot;
    const resolved = artifactPath(path, root, undefined, true);
    if (!resolved) throw new Error("文件不在当前工作区内。");
    await window.eidosRuntime.showItemInFolder(`${root.replace(/\/$/, "")}/${resolved}`);
  }

  const handleOpenFileInDock = useCallback((path: string, position?: WorkspaceFilePosition): void => {
    if (!currentSnapshot) return;
    const root = currentSnapshot.session.worktree?.worktreeRoot ?? currentSnapshot.session.workspaceRoot;
    const resolved = artifactPath(path, root, undefined, true);
    if (!resolved) return;
    openTool("files");
    setExplorerOpenRequest({
      path: resolved,
      requestId: ++fileOpenSequenceRef.current,
      ...(position?.line === undefined ? {} : { line: position.line }),
      ...(position?.column === undefined ? {} : { column: position.column }),
    });
  }, [currentSnapshot, openTool]);

  function toggleDock(): void {
    if (dockOpen) {
      setDockOpen(false);
      setDockExpanded(false);
      return;
    }
    setDockOpen(true);
  }

  function closeTab(tabId: string): void {
    if (browserRequest?.browserId === tabId) setBrowserRequest(undefined);
    setOpenTabs((current) => {
      const index = current.findIndex((tab) => tab.id === tabId);
      const next = current.filter((tab) => tab.id !== tabId);
      if (next.length === 0) {
        setActiveTabId(undefined);
        setDockOpen(false);
        setDockExpanded(false);
      } else if (activeTabId === tabId) {
        setActiveTabId(next[Math.min(index, next.length - 1)]?.id);
      }
      return next;
    });
  }

  function openGitWorkflow(): void {
    if (environmentPopoverRef.current) environmentPopoverRef.current.open = false;
    setEnvironmentPopoverOpen(false);
    setWorkflowOpenRequest((request) => request + 1);
  }

  const isRenamingThisSession = Boolean(snapshot && renamingSessionId === snapshot.session.id);

  const sidebarDisabled =
    sessionState.pending.creatingSession === true
    || handoffBusy
    || !isStorageReady;

  const workspaceEnvironment = currentSnapshot && !isDraft && availableTools.length > 0 ? (
    <div className="workspace-header-tools">
        <details
          ref={environmentPopoverRef}
          className="environment-popover"
          key={executionKey}
          open={environmentPopoverOpen}
        >
          <summary
            className="icon-button"
            role="button"
            aria-label={pendingAgentApprovals > 0 ? `环境信息，${pendingAgentApprovals} 条子任务待审批` : "环境信息"}
            onClick={(event) => {
              event.preventDefault();
              setEnvironmentPopoverOpen((open) => !open);
            }}
          >
            <svg viewBox="0 0 20 20" data-icon="environment" aria-hidden="true">
              <circle cx="4.5" cy="5" r="1.5" />
              <circle cx="4.5" cy="10" r="1.5" />
              <circle cx="4.5" cy="15" r="1.5" />
              <path d="M9 5h7M9 10h7M9 15h7" />
            </svg>
            {pendingAgentApprovals > 0 && <span className="agent-approval-badge" aria-hidden="true">{pendingAgentApprovals}</span>}
          </summary>
          <section
            className="environment-popover__panel"
            aria-label="环境信息预览"
            hidden={!environmentPopoverOpen}
          >
            <header>
              <h2>环境信息</h2>
            </header>
            <div className="environment-popover__body">
              <OutputContent
                artifacts={outputArtifacts}
                loading={completeSessionItems.loading}
                error={completeSessionItems.error}
              />
              {planning.state?.plans && planning.state.plans.length > 0 && (
                <div className="environment-popover__section">
                  <div className="environment-popover__section-title">计划</div>
                  <button
                    type="button"
                    className="environment-popover__row environment-popover__row--plan"
                    onClick={handleOpenPlan}
                  >
                    <span className="plan-icon" aria-hidden="true">
                      <PlanLightbulbIcon />
                    </span>
                    <span className="plan-title">{planning.state.plans[0]?.title ?? "计划"}</span>
                  </button>
                </div>
              )}
              {agents.state?.agents && agents.state.agents.length > 0 && (
                <div className="environment-popover__section">
                  <div className="environment-popover__section-title">智能体 · {agents.state.agents.length}{pendingAgentApprovals > 0 ? ` · 待审批 ${pendingAgentApprovals}` : ""}</div>
                  <AgentList agents={agents.state.agents} onOpen={openAgent} approvalCounts={agentApprovalCounts} models={feedModels} compact />
                </div>
              )}
              {agents.error && <p role="alert">{agents.error}</p>}
              {sessionHasGit && (
                <button type="button" className="environment-popover__row" onClick={() => openTool("review")}>
                  <span>变更</span>
                  <span className="git-line-summary" aria-label="修改行数">
                    <ins>+{gitReviewState.summary?.additions ?? 0}</ins>
                    <del>-{gitReviewState.summary?.deletions ?? 0}</del>
                  </span>
                </button>
              )}
              {sessionHasGit && (
                <div className="environment-popover__row">
                  <span>{sessionIsLocal ? "本地" : "本地工作树"}</span>
                  <Button
                    variant="ghost"
                    size="small"
                    disabled={Boolean(activeRun) || handoffBusy}
                    loading={handoffBusy}
                    onClick={() => setHandoffSessionId(snapshot!.session.id)}
                  >
                    更改工作环境
                  </Button>
                </div>
              )}
              {sessionHasGit && (
                <div className="environment-popover__row environment-popover__branch">
                  <span>{sessionBranch ?? `分离状态 @ ${(gitReviewState.status?.head ?? "").slice(0, 7)}`}</span>
                  <span aria-hidden="true">→</span>
                  <span>{gitReviewState.summary?.compareRef ?? gitReviewState.status?.baseRef ?? "HEAD"}</span>
                </div>
              )}
              {sessionHasGit && (
                <button type="button" className="environment-popover__row" onClick={openGitWorkflow}>
                  <span>提交或推送</span>
                </button>
              )}
            </div>
          </section>
        </details>
    </div>
  ) : null;

  const workspaceToggle = availableTools.length > 0
    ? <WorkspaceDockToggle open={dockOpen} onClick={toggleDock} />
    : null;


  return (
    <ArtifactProvider value={currentSnapshot ? {
      sessionId: currentSnapshot.session.id,
      executionRoot: currentSnapshot.session.worktree?.worktreeRoot ?? currentSnapshot.session.workspaceRoot,
      ...(currentSnapshot.session.project?.id
        ? { projectId: currentSnapshot.session.project.id }
        : {}),
      openFile: handleOpenFileInDock,
      openBrowser: handleOpenBrowser,
      openExternal: handleOpenExternal,
      openReview: handleOpenReview,
      showInFinder: handleShowInFinder,
      showToast,
    } : undefined}>
    <InputContextProvider ready={runState.draftReady && isStorageReady} sessionId={currentSnapshot?.session.id} workspaceRoot={currentSnapshot?.session.worktree?.worktreeRoot ?? currentSnapshot?.session.workspaceRoot}
      onAdd={runActions.addReference} onSettings={(section) => { setSettingsCategory(section === "agents" ? "agents" : section === "mcp" ? "mcp" : section === "skills" ? "skills" : "plugins"); setSettingsOpen(true); setDockOpen(false); }}
      onNavigateToSession={(target) => {
        if (settingsOpen) setSettingsOpen(false);
        navHistoryActions.navigateTo(target);
        handleNavigateToSession(target);
      }}>
    <main className={`workbench${sidebarOpen && !settingsOpen && !automationsOpen ? "" : " workbench--sidebar-collapsed"}${settingsOpen ? " workbench--settings" : ""}${automationsOpen ? " workbench--settings workbench--automations" : ""}`}>
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
      <SessionSidebar
        onSearch={() => setSearchMode("global")}
        collapsed={!sidebarOpen || settingsOpen || automationsOpen}
        sessions={sessionState.sessions}
        projects={sessionState.projects}
        pins={sidebarPins.pins}
        pinningDisabled={!sidebarPins.loaded || !isStorageReady}
        pendingPinTargets={sidebarPins.pending}
        onSetPinned={(target, pinned) => void sidebarPins.setPinned(target, pinned)}
        selectedId={sessionState.navigationSessionId ?? currentSnapshot?.session.id}
        disabled={sidebarDisabled}
        readCompletedSessions={sessionState.readCompletedSessions}
        runtimePresentation={runtimePresentation}
        isSelectingSessionId={sessionState.pending.selectingSessionId}
        gitStatusBySessionId={gitReviewState.statusBySessionId}
        onCreate={() => { setAutomationsOpen(false); handleCreateSession(); }}
        onCreateInProject={handleCreateInProject}
        onSelect={(session) => { setAutomationsOpen(false); void handleSelectSession(session); }}
        onRename={(session) => void beginRename(session)}
        onDelete={(session) => requestDeleteSession(session)}
        onDeleteProject={(project) => requestDeleteProject(project)}
        onShowInFinder={(project) => {
          void window.eidosRuntime.showItemInFolder(project.workspaceRoot).catch(() => undefined);
        }}
        onOpenSettings={() => {
          setAutomationsOpen(false);
          setSettingsOpen(true);
          setDockOpen(false);
          setDockExpanded(false);
          setRenamingSessionId(undefined);
        }}
        onOpenAutomations={() => { setAutomationDetailId(undefined); setAutomationsOpen(true); setSettingsOpen(false); setDockOpen(false); setDockExpanded(false); }}
        onOpenAutomation={(id) => { setAutomationDetailId(id); setAutomationsOpen(true); setSettingsOpen(false); setDockOpen(false); setDockExpanded(false); }}
        navigationSlot={
          <NavigationControls
            sidebarOpen={sidebarOpen}
            canGoBack={canGoBack}
            canGoForward={canGoForward}
            onToggleSidebar={toggleSidebar}
            onGoBack={handleGoBack}
            onGoForward={handleGoForward}
            showTrafficSpacer={true}
          />
        }
      />

      {searchMode === "global" && <ChatSearch key="global-search" mode="global" onClear={clearSearchTarget} projects={sessionState.projects} sessions={sessionState.sessions} onClose={() => { setSearchMode(undefined); setSearchTarget(undefined); }} onSelect={handleSearchSelect} />}
      <section ref={workspaceRef} className="workspace" aria-label="Agent 工作区" tabIndex={-1}>
        {currentSnapshot?.session.worktreeRestoreAvailable === true && !isDraft && (
          <div className="worktree-restore-banner" role="status">
            <span>本地工作树已清理，以释放磁盘空间</span>
            <Button
              variant="secondary"
              size="small"
              disabled={restoreBusy}
              loading={restoreBusy}
              onClick={() => void sessionActions.restoreWorktree(snapshot!.session.id)}
            >
              恢复工作树
            </Button>
          </div>
        )}

        {automationsOpen ? (
          <AutomationPage
            initialAutomationId={automationDetailId}
            projects={sessionState.projects}
            sessions={sessionState.sessions}
            models={modelState.list?.models ?? []}
            currentSessionId={isDraft ? undefined : currentSnapshot?.session.id}
            currentProjectId={currentSnapshot?.session.project?.id}
            onClose={() => setAutomationsOpen(false)}
            onOpenSession={(id) => { setAutomationsOpen(false); handleNavigateToSession(id); }}
            runtimeVersion={runtimeStatus.state === "ready" ? runtimeStatus.runtimeVersion : "0.x"}
          />
        ) : settingsOpen ? (
          <SettingsPage
            initialCategory={settingsCategory}
            sessionId={activeSnapshot?.session.id}
            onOpenMemorySource={(id) => {
              const source = sessionState.sessions.find((session) => session.id === id);
              if (source) {setSettingsOpen(false); void handleSelectSession(source);}
            }}
            runtime={runtimeStatus}
            modelList={modelState.list}
            modelLoading={modelState.loading}
            modelError={modelState.error}
            plugins={extensionState.plugins}
            skills={extensionState.skills}
            mcpServers={extensionState.mcpServers}
            extensionError={extensionState.error}
            pendingAction={extensionState.pendingAction}
            hasBlockingModal={hasBlockingModal}
            onClose={() => setSettingsOpen(false)}
            onModelsChanged={() => modelActions.load()}
            onImportPlugin={() => extensionActions.importPlugin()}
            onTogglePlugin={(id, enabled) => extensionActions.setPluginEnabled(id, enabled)}
            onRemovePlugin={(id) => extensionActions.removePlugin(id)}
            onToggleSkill={(id, enabled) => extensionActions.setSkillEnabled(id, enabled)}
            onRemoveSkill={(id) => extensionActions.removeSkill(id)}
            onToggleMcp={(pId, sId, enabled) => extensionActions.setMcpEnabled(pId, sId, enabled)}
            onCreateMcp={(input) => extensionActions.createMcpServer(input)}
            onUpdateMcp={(input) => extensionActions.updateMcpServer(input)}
            onRemoveMcp={(serverId) => extensionActions.removeMcpServer(serverId)}
          />
        ) : currentSnapshot ? (
          <>

            <div
              ref={workspaceBodyRef}
              className={`workspace-body${dockOpen ? " workspace-body--with-dock" : " workspace-body--session-centered"}${dockExpanded ? " workspace-body--expanded" : ""}`}
              style={dockWidth === undefined ? undefined : {
                "--workspace-dock-width": `${dockWidth}px`,
              } as CSSProperties}
            >
            <div className={`workspace-main-column${dockExpanded ? " workspace-main-column--hidden" : ""}`} inert={dockExpanded}>
            {searchMode === "session" && !isDraft && <ChatSearch key={currentSnapshot.session.id} mode="session" onClear={clearSearchTarget} sessionId={currentSnapshot.session.id} projects={sessionState.projects} onClose={() => { setSearchMode(undefined); setSearchTarget(undefined); }} onSelect={handleSearchSelect} />}
            <header className={`workspace-header session-header${isDraft ? " session-header--draft" : ""}`}>
              {!isDraft && (isRenamingThisSession ? (
                <form
                  className="rename-form"
                  onSubmit={(e) => { e.preventDefault(); void submitRename(); }}
                >
                  <label className="sr-only" htmlFor="session-title">任务标题</label>
                  <input
                    id="session-title"
                    value={titleDraft}
                    autoFocus
                    onChange={(e) => setTitleDraft(e.target.value)}
                  />
                  <Button
                    type="submit"
                    variant="primary"
                    size="small"
                    disabled={
                      sessionState.pending.renamingSessionId === snapshot!.session.id
                      || !titleDraft.trim()
                    }
                    loading={sessionState.pending.renamingSessionId === snapshot!.session.id}
                  >
                    保存
                  </Button>
                  <Button
                    variant="ghost"
                    size="small"
                    onClick={() => { setRenamingSessionId(undefined); setRenameError(undefined); }}
                  >
                    取消
                  </Button>
                  {renameError && <span className="approval-error" role="alert">{renameError}</span>}
                </form>
              ) : (
                <div className="session-title-group">
                  <h1>{currentSnapshot.session.title ?? "新会话"}</h1>
                  {!isDraft && (
                    <DropdownMenu
                      trigger="•••"
                      label="任务菜单"
                      items={[
                        {
                          key: "rename",
                          label: "编辑标题",
                          icon: <EditIcon />,
                          onClick: () => void beginRename(currentSnapshot.session),
                        },
                        {
                          key: "delete",
                          label: "删除任务",
                          icon: <TrashIcon />,
                          danger: true,
                          disabled: Boolean(activeRun) || handoffBusy,
                          onClick: () => requestDeleteSession(currentSnapshot.session),
                        },
                      ]}
                    />
                  )}
                </div>
              ))}
              {workspaceToggle && (
                <div className="session-header-actions">{workspaceEnvironment}{!dockOpen && workspaceToggle}</div>
              )}
            </header>


            <div className="workspace-content">
              <div className="workspace-main">
                <InputDropZone>
                {responseActionState.error && (
                  <p className="approval-error response-action-error" role="alert">
                    {responseActionState.error}
                  </p>
                )}
                <SessionCanvas
                  sessionId={currentSnapshot.session.id}
                  welcome={showWelcome}
                  feed={<ExecutionFeed
                  key={currentSnapshot.session.id}
                  searchTarget={searchTarget}
                  items={currentSnapshot.items.filter((item) => !currentSnapshot.runs.some((run) => run.id === item.runId && isQueuedMessage(run)))}
                  resultItems={completeSessionItems.items}
                  contextCompactions={currentSnapshot.contextCompactions}
                  projectless={currentSnapshot.session.projectless === true}
                  runs={currentSnapshot.runs.filter((run) => !isQueuedMessage(run))}
                  models={feedModels}
                  workspaceRoot={currentSnapshot.session.workspaceRoot}
                  responseActionState={responseActionState.responseState}
                  pendingFeedbackItemIds={responseActionState.pendingFeedbackItemIds}
                  revisionSubmitting={runState.isSubmitting}
                  stepResolutions={currentSnapshot.stepResolutions}
                  approvals={feedApprovals}
                  respondingApprovalIds={respondingApprovalIds}
                  respondingKindByApprovalId={respondingKindByApprovalId}
                  expiredApprovalIds={approvalState.expiredApprovalIds}
                  errorsByApprovalId={errorsByApprovalId}
                  approvalLoadError={approvalState.pendingApprovalsLoadError}
                  loadingPendingApprovals={approvalState.loadingPendingApprovals}
                  onRetryLoadPending={handleFeedRetryLoadPending}
                  onApprove={handleFeedApprove}
                  onReject={handleFeedReject}
                  onFeedback={handleFeedFeedback}
                  onFork={handleFeedFork}
                  onLoadOlder={currentSnapshot.previousItemId ? () => void sessionActions.loadOlderSession() : undefined}
                  loadingOlder={sessionState.pending.loadingHistorySessionId === currentSnapshot.session.id}
                  onRegenerate={handleFeedRegenerate}
                  onEditResend={handleFeedEditResend}
                  onOpenFile={handleOpenFileInDock}
                  onOpenPlan={handleOpenPlan}
                  agents={feedAgents}
                  onOpenSubagent={handleOpenSubagent}
                />}
                >

                {!isDraft && <QueuedMessages
                  key={`queue-${currentSnapshot.session.id}`}
                  sessionId={currentSnapshot.session.id}
                  activeRunId={activeRun && ["running", "waiting_approval", "waiting_input", "waiting_agents"].includes(activeRun.status) ? activeRun.id : undefined}
                  ready={isStorageReady && runtimeStatus.state === "ready"}
                  onChanged={(change) => {
                    sessionActions.setSnapshot((previous) => applyQueuedRunChange(previous, change));
                    void sessionActions.refreshCompletedSession(change.sessionId);
                  }}
                  onRefresh={() => void sessionActions.refreshCompletedSession(currentSnapshot.session.id)}
                />}
                <ComposerSlot
                  run={activeRun}
                  approval={approvals.find((a) => a.runId === activeRun?.id)}
                  pendingUserInput={planning.pendingQuestion}
                  userInputReady={isStorageReady && runtimeStatus.state === "ready"}
                  onAnswerUserInput={planning.refresh}
                  respondingApprovalIds={respondingApprovalIds}
                  respondingKindByApprovalId={respondingKindByApprovalId}
                  expiredApprovalIds={approvalState.expiredApprovalIds}
                  errorsByApprovalId={errorsByApprovalId}
                  onApprove={(request) => void approvalActions.approve(request)}
                  onReject={(request) => void approvalActions.reject(request)}
                >
                <Composer
                  ref={composerRef}
                  composerMode={worktreeRestoreRequired ? "read_only" : composerMode}
                  activeRun={activeRun}
                  input={input}
                  references={runState.references}
                  onRemoveReference={runActions.removeReference}
                  draftReady={runState.draftReady}
                  modelList={modelState.list}
                  selectedModelId={modelState.selectedModelId}
                  workMode={workMode}
                  onWorkModeChange={(mode) => {
                    if (approvalSessionId) setWorkModes((previous) => ({ ...previous, [approvalSessionId]: mode }));
                  }}
                  approvalMode={approvalMode}
                  onApprovalModeChange={(mode) => {
                    if (approvalSessionId) setApprovalModes((previous) => ({ ...previous, [approvalSessionId]: mode }));
                  }}
                  reasoningSelection={modelState.reasoningSelection}
                  contextUsage={contextUsageState.usage}
                  contextRunId={contextRun?.id}
                  contextDetails={contextUsageState.details}
                  showContextIndicator={Boolean(contextRun)}
                  modelConfigured={Boolean(modelState.list?.models.length)}
                  modelLoading={modelState.loading}
                  isSubmitting={runState.isSubmitting || sessionState.pending.creatingSession === true || handoffBusy || restoreBusy}
                  submitKind={runState.submitKind}
                  cancelingRunId={runState.cancelingRunId}
                  onInputChange={runActions.setInput}
                  onSubmit={handleSubmit}
                  onCancel={() => activeRun && !isDraft && snapshot && void runActions.cancelRun({ runId: activeRun.id, sessionId: snapshot.session.id })}
                  onModelChange={(id) => modelActions.selectModel(id)}
                  onReasoningSelectionChange={(selection) => {
                    if (modelState.selectedModelId) {
                      modelActions.setReasoningSelection(modelState.selectedModelId, selection);
                    }
                  }}
                  onOpenModelSettings={() => {
                    setSettingsOpen(true);
                    setDockOpen(false);
                    setDockExpanded(false);
                  }}
                  showSessionContext={showSessionSetup}
                  project={sessionProject ?? null}
                  projectless={currentSnapshot.session.projectless === true}
                  executionMode={currentSnapshot.session.executionMode}
                  branch={sessionBranch}
                  branches={sessionIsLocal ? gitReviewState.projectContext?.branches : undefined}
                  onBranchChange={sessionIsLocal ? (branch) => void switchLocalBranch(branch) : undefined}
                  branchChanging={sessionState.pending.branchSessionId === currentSnapshot.session.id}
                  onSelectProject={() => void handleSelectProject()}
                  onLeaveProject={() => handleCreateSession()}
                  onExecutionModeChange={sessionProject?.gitAvailable === true ? requestExecutionModeChange : undefined}
                />
                </ComposerSlot>
                </SessionCanvas>
                </InputDropZone>
              </div>

            </div>
            </div>



            {dockOpen && !dockExpanded && (
              <div
                className="workspace-dock-resize-handle"
                role="separator"
                aria-label="调整工作区宽度"
                aria-orientation="vertical"
                aria-valuemin={getDockResizeBounds().min}
                aria-valuemax={getDockResizeBounds().max}
                aria-valuenow={dockWidth ?? getCurrentDockWidth()}
                tabIndex={0}
                onPointerDown={handleDockResizePointerDown}
                onPointerMove={handleDockResizePointerMove}
                onPointerUp={finishDockResize}
                onPointerCancel={finishDockResize}
                onKeyDown={handleDockResizeKeyDown}
              />
            )}

            <div className="workspace-drawer" inert={!dockOpen} aria-hidden={!dockOpen}>
            {(dockOpen || dockRetained) && availableTools.length > 0 && (
              <WorkspaceDock
                actions={workspaceToggle}
                activeTabId={activeTabId}
                availableTools={availableTools}
                expanded={dockExpanded}
                openTabs={openTabs}
                onAddTool={openTool}
                onCloseTab={closeTab}
                onSelectTab={setActiveTabId}
                onToggleExpanded={() => setDockExpanded((expanded) => !expanded)}
                renderTab={(tab) => {
                  if (tab.kind === "agent") return <AgentWorkspacePanel
                    key={`${currentSnapshot.session.id}:${tab.id}`}
                    agents={agents.state?.agents ?? []}
                    agentId={tab.id.startsWith("agent-") ? tab.id.slice(6) : undefined}
                    models={feedModels}
                    error={agents.error}
                    stopping={agents.stopping}
                    onOpen={openAgent}
                    onStop={(agent) => { void agents.stop(agent); }}
                    approvals={approvals}
                    respondingApprovalIds={respondingApprovalIds}
                    respondingKindByApprovalId={respondingKindByApprovalId}
                    expiredApprovalIds={approvalState.expiredApprovalIds}
                    errorsByApprovalId={errorsByApprovalId}
                    onApprove={(request) => void approvalActions.approve(request)}
                    onReject={(request) => void approvalActions.reject(request)}
                    onOpenFile={handleOpenFileInDock}
                    onOpenPlan={handleOpenPlan}
                    workspaceRoot={currentSnapshot.session.workspaceRoot}
                    onBackToList={openAgentList}
                    expanded={dockExpanded}
                  />;
                  if (tab.kind === "text-review" && reviewRequest) return <TextReviewPanel
                    key={`${executionKey}:${reviewRequest.requestId}`} sessionId={currentSnapshot.session.id}
                    runId={reviewRequest.runId} path={reviewRequest.path} items={completeSessionItems.items}
                    loading={completeSessionItems.loading} error={completeSessionItems.error}
                    onFeedback={handleReviewFeedback} disabled={Boolean(activeRun) || runState.isSubmitting}
                    onRefresh={() => currentSnapshot && void sessionActions.selectSession(currentSnapshot.session)}
                    expanded={dockExpanded} />;
                  if (tab.kind === "plan") {
                    return (
                      <PlanPanel
                        key={currentSnapshot.session.id}
                        sessionId={currentSnapshot.session.id}
                        plan={planning.state?.plans[0]}
                        historyPlans={planning.state?.plans && planning.state.plans.length > 1 ? planning.state.plans.slice(1) : []}
                        ready={isStorageReady && runtimeStatus.state === "ready"}
                        canEdit={!activeRun && !runState.isSubmitting && !worktreeRestoreRequired}
                        onExecute={(plan) => submitPlan(plan)}
                        onRevise={(plan, feedback) => submitPlan(plan, feedback)}
                        onSaved={planning.refresh}
                      />
                    );
                  }
                  if (tab.kind === "review") {
                    return sessionHasGit ? (
                      <GitChangesPanel
                        sessionId={currentSnapshot.session.id}
                        workspaceRoot={currentSnapshot.session.project?.workspaceRoot ?? currentSnapshot.session.workspaceRoot}
                        items={completeSessionItems.items}
                        latestRunId={currentSnapshot.runs.filter((run) => !run.inherited).at(-1)?.id}
                        previousItemId={currentSnapshot.previousItemId}
                        lastTurnRequest={reviewRequest}
                        scope={gitReviewState.scope}
                        status={gitReviewState.status}
                        summary={gitReviewState.summary}
                        loading={gitReviewState.loadingStatus || gitReviewState.loadingSummary}
                        error={gitReviewState.error}
                        onScopeChange={gitReviewActions.selectScope}
                        onRefresh={gitReviewActions.refresh}
                        onSendReviewFeedback={handleReviewFeedback}
                        reviewFeedbackDisabled={Boolean(activeRun) || runState.isSubmitting}
                        workflowDisabled={
                          isDraft
                          || runState.isSubmitting
                          || handoffBusy
                          || sessionState.pending.branchSessionId === currentSnapshot.session.id
                          || sessionState.pending.creatingBranchSessionId === currentSnapshot.session.id
                        }
                        workspaceMutationDisabled={Boolean(activeRun)}
                        onCreateBranch={
                          !isDraft && (sessionIsLocal || (sessionWorktree?.state === "active" && sessionWorktree.branch === null))
                            ? () => openCreateBranch(currentSnapshot.session.id, sessionIsLocal ? "local" : "worktree")
                            : undefined
                        }
                        expanded={dockExpanded}
                      />
                    ) : null;
                  }
                  if (tab.kind === "terminal") {
                    return (
                      <Suspense fallback={<p className="terminal-panel__message" role="status">正在准备终端…</p>}>
                        <TerminalPanel
                          key={tab.id}
                          sessionId={currentSnapshot.session.id}
                          workspaceRoot={currentSnapshot.session.worktree?.worktreeRoot ?? currentSnapshot.session.workspaceRoot}
                          {...(currentSnapshot.session.project?.id
                            ? { projectId: currentSnapshot.session.project.id }
                            : {})}
                          active={activeTabId === tab.id}
                        />
                      </Suspense>
                    );
                  }
                  if (tab.kind === "browser") return <BrowserPanel
                    key={executionKey} browserId={tab.id} sessionId={currentSnapshot.session.id} executionKey={executionKey}
                    workspaceRoot={currentSnapshot.session.worktree?.worktreeRoot ?? currentSnapshot.session.workspaceRoot}
                    active={dockOpen && activeTabId === tab.id}
                    request={browserRequest?.browserId === tab.id ? { url: browserRequest.url, id: browserRequest.id } : undefined}
                    onPageStateChange={(pageState) => {
                      setOpenTabs((currentTabs) => currentTabs.map((t) => {
                        if (t.id !== tab.id) return t;
                        const title = pageState.title?.trim() || (pageState.url ? getBrowserTabTitle(pageState.url) : t.title);
                        if (t.title === title && t.loading === pageState.loading) return t;
                        return {
                          ...t,
                          ...(title ? { title } : {}),
                          loading: pageState.loading,
                        };
                      }));
                    }}
                  />;
                  return (
                  <WorkspaceExplorer
                      sessionId={currentSnapshot.session.id}
                      executionKey={executionKey}
                      layout={dockExpanded ? "expanded" : "side"}
                      openRequest={explorerOpenRequest}
                      {...(isDraft && currentSnapshot.session.workspaceRoot ? {
                        listDirectory: (sid: string, path: string) =>
                          window.eidosRuntime.listWorkspaceDirectory(
                            sid,
                            path,
                            undefined,
                            currentSnapshot.session.workspaceRoot,
                            currentSnapshot.session.project?.id,
                          ),
                        readPreview: (sid: string, path: string) =>
                          window.eidosRuntime.readWorkspaceFilePreview(
                            sid,
                            path,
                            currentSnapshot.session.workspaceRoot,
                            currentSnapshot.session.project?.id,
                          ),
                      } : {})}
                    />
                  );
                }}
              />
            )}
            </div>
            </div>
          </>
        ) : (
          <>
            <header className="workspace-header workspace-header--empty" aria-hidden="true" />
            <WelcomeMessage />
        </>
      )}
      </section>

      {!sidebarOpen && !settingsOpen && !automationsOpen && (
        <div className="collapsed-navigation-bar">
          <NavigationControls
            sidebarOpen={sidebarOpen}
            canGoBack={canGoBack}
            canGoForward={canGoForward}
            onToggleSidebar={toggleSidebar}
            onGoBack={handleGoBack}
            onGoForward={handleGoForward}
            showTrafficSpacer={true}
          />
        </div>
      )}

      <ProjectPicker
        open={projectPickerOpen}
        projects={sessionState.projects}
        selectedProjectId={currentSnapshot?.session.project?.id}
        anchorRef={composerRef}
        getFallbackFocus={getDialogFallbackFocus}
        onSelect={handleSelectProjectFromPicker}
        onCreate={handleOpenCreateProject}
        onClose={() => setProjectPickerOpen(false)}
      />
      {currentSnapshot && !isDraft && sessionHasGit && gitReviewState.status && (
        <GitWorkflowControls
          sessionId={currentSnapshot.session.id}
          workspaceRoot={currentSnapshot.session.project?.workspaceRoot ?? currentSnapshot.session.workspaceRoot}
          status={gitReviewState.status}
          disabled={
            Boolean(activeRun)
            || runState.isSubmitting
            || handoffBusy
            || sessionState.pending.branchSessionId === currentSnapshot.session.id
            || sessionState.pending.creatingBranchSessionId === currentSnapshot.session.id
          }
          onRefresh={gitReviewActions.refresh}
          openRequest={workflowOpenRequest}
          onClose={() => setWorkflowOpenRequest(0)}
          dialogOnly
          onCreateBranch={
            sessionIsLocal || (sessionWorktree?.state === "active" && sessionWorktree.branch === null)
              ? () => openCreateBranch(currentSnapshot.session.id, sessionIsLocal ? "local" : "worktree")
              : undefined
          }
        />
      )}
      <CreateProjectDialog
        open={createProjectOpen}
        sourceFolder={createProjectFolder}
        busy={createProjectBusy}
        error={createProjectError}
        getFallbackFocus={getDialogFallbackFocus}
        onCreate={(name, folder) => void handleCreateProject(name, folder)}
        onSelectFolder={() => void handleSelectProjectFolder()}
        onCancel={() => {
          if (createProjectBusy) return;
          setCreateProjectOpen(false);
          setCreateProjectError(undefined);
        }}
      />
      <CreateBranchDialog
        open={Boolean(createBranchSessionId)}
        mode={createBranchMode}
        busy={sessionState.pending.creatingBranchSessionId !== undefined}
        error={sessionState.error}
        getFallbackFocus={getDialogFallbackFocus}
        onConfirm={(branch) => void confirmCreateBranch(branch)}
        onCancel={() => {
          setCreateBranchSessionId(undefined);
          setCreateBranchMode("worktree");
          sessionActions.setError(undefined);
        }}
      />
      <HandoffDialog
        open={Boolean(handoffSessionId)}
        currentMode={currentSnapshot?.session.executionMode ?? "local"}
        currentBranch={sessionBranch}
        branches={gitReviewState.projectContext?.branches ?? []}
        associatedWorktreeId={currentSnapshot?.session.associatedWorktreeId}
        changedFileCount={gitReviewState.projectContext?.changedFileCount ?? 0}
        busy={handoffBusy}
        error={sessionState.error}
        getFallbackFocus={getDialogFallbackFocus}
        onConfirm={(target, branch) => void confirmHandoff(target, branch)}
        onCancel={() => {
          if (handoffBusy) return;
          setHandoffSessionId(undefined);
          sessionActions.setError(undefined);
        }}
      />
      <ForkConversationDialog
        open={forkSelection !== null}
        session={forkSelection?.session}
        forkTarget={forkTarget}
        busy={sessionState.pending.creatingSession === true}
        error={sessionState.error}
        onTargetChange={setForkTarget}
        onCancel={() => setForkSelection(null)}
        onConfirm={() => { if (forkSelection) void createConversationFork(forkSelection.session, forkSelection.item, forkTarget); }}
      />
      <ConfirmDialog
        open={Boolean(sessionToDelete)}
        title={`删除任务"${sessionToDelete?.title ?? "新会话"}"？`}
        description="项目文件不会被删除。删除后无法撤销。"
        confirmLabel="删除"
        cancelLabel="取消"
        isDestructive
        busy={deleteBusy}
        error={deleteError}
        getFallbackFocus={getDialogFallbackFocus}
        onConfirm={() => void confirmDelete()}
        onCancel={() => { setSessionToDelete(undefined); setDeleteError(undefined); }}
      />
      <ConfirmDialog
        open={Boolean(projectToDelete)}
        title={`删除项目"${projectToDelete?.name ?? projectToDelete?.workspaceRoot.split("/").filter(Boolean).at(-1) ?? projectToDelete?.workspaceRoot ?? "项目"}"？`}
        description="只会删除 Eidos 中的项目记录，不会删除项目文件或 Git 仓库。"
        confirmLabel="删除"
        cancelLabel="取消"
        isDestructive
        busy={projectDeleteBusy}
        error={projectDeleteError}
        getFallbackFocus={getDialogFallbackFocus}
        onConfirm={() => void confirmDeleteProject()}
        onCancel={() => { setProjectToDelete(undefined); setProjectDeleteError(undefined); }}
      />
    </main>
    </InputContextProvider>
    </ArtifactProvider>
  );
}

function PlanLightbulbIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 2a4.5 4.5 0 0 0-3 7.8V11.5a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1V9.8A4.5 4.5 0 0 0 8 2z" />
      <path d="M6.5 14h3" />
    </svg>
  );
}
