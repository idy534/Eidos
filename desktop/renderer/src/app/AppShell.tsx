import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import type { Project, Run, Session } from "../contracts.js";
import { SettingsPage } from "../components/settings/SettingsPage.js";
import { ExecutionFeed } from "../components/ExecutionFeed.js";
import { EidosMark } from "../components/EidosMark.js";
import { SessionSidebar } from "../components/SessionSidebar.js";
import { Button } from "../components/Button.js";
import { DropdownMenu } from "../components/DropdownMenu.js";
import { PrimaryActionButton } from "../components/PrimaryActionButton.js";
import { ConfirmDialog } from "../components/settings/ConfirmDialog.js";
import { CreateBranchDialog } from "../components/CreateBranchDialog.js";
import { HandoffDialog } from "../components/HandoffDialog.js";
import { ProjectPicker } from "../components/ProjectPicker.js";
import { CreateProjectDialog } from "../components/CreateProjectDialog.js";
import { Composer } from "../components/Composer.js";
import { GitChangesPanel } from "../components/GitChangesPanel.js";
import { WorkspaceExplorer } from "../components/WorkspaceExplorer.js";
import {
  WorkspaceDock,
  type WorkspaceToolKind,
} from "../components/WorkspaceDock.js";
import type { RuntimeLifecycleState } from "./useRuntimeLifecycle.js";
import { useSessionController } from "./useSessionController.js";
import { useRunController } from "./useRunController.js";
import { useApprovalController } from "./useApprovalController.js";
import { useModelController } from "./useModelController.js";
import { useResponseActionController } from "./useResponseActionController.js";
import { useContextUsageController } from "./useContextUsageController.js";
import { resolveSessionModelId } from "./session-model-resolver.js";
import { useExtensionController } from "./useExtensionController.js";
import { useGitReviewController } from "./useGitReviewController.js";
import { applyNotification, userFacingError } from "../session-state.js";
import { IPC } from "../../../shared/ipc-channels.js";

interface AppShellProps {
  runtime: RuntimeLifecycleState;
}

type CreateBranchMode = "local" | "worktree";

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
  const activeSnapshot = sessionState.snapshot ?? sessionState.draft;
  const [runState, runActions] = useRunController(activeSnapshot, isStorageReady);
  const [approvalState, approvalActions] = useApprovalController();
  const [modelState, modelActions] = useModelController();
  const [extensionState, extensionActions] = useExtensionController();
  const [gitReviewState, gitReviewActions] = useGitReviewController({
    ready: runtimeStatus.state === "ready" && isStorageReady,
    session: sessionState.snapshot?.session,
  });
  const [responseActionState, responseActionActions] = useResponseActionController(
    sessionState.snapshot?.session.id,
  );
  const latestRun = sessionState.snapshot?.runs.length
    ? sessionState.snapshot.runs[sessionState.snapshot.runs.length - 1]
    : undefined;
  const contextRun = runState.activeRun ?? latestRun;
  const contextRunId = contextRun && contextRun.modelId === modelState.selectedModelId
    ? contextRun.id
    : undefined;
  const [contextUsageState, contextUsageActions] = useContextUsageController({
    ready: runtimeStatus.state === "ready" && isStorageReady,
    sessionId: sessionState.snapshot?.session.id,
    modelId: modelState.selectedModelId,
    runId: contextRunId,
  });
  const handleContextUsageNotification = contextUsageActions.handleNotification;

  // UI-only state (not domain state)
  const [settingsOpen, setSettingsOpen] = useState(false);
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
  const [dockOpen, setDockOpen] = useState(false);
  const [dockExpanded, setDockExpanded] = useState(false);
  const [openTools, setOpenTools] = useState<WorkspaceToolKind[]>([]);
  const [activeTool, setActiveTool] = useState<WorkspaceToolKind>("review");
  const [selectedFile, setSelectedFile] = useState<string | undefined>(undefined);
  const [workflowOpenRequest, setWorkflowOpenRequest] = useState(0);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const modelSessionInitializedRef = useRef<string | undefined>(undefined);
  const getDialogFallbackFocus = useCallback((): HTMLElement | null => {
    const composer = composerRef.current;
    return composer?.isConnected && !composer.disabled
      ? composer
      : workspaceRef.current;
  }, []);

  const topError = sessionState.error ?? runState.error;

  // -----------------------------------------------------------------------
  // Bootstrap: load sessions, model, approvals independently
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (runtimeStatus.state !== "ready" || runtimeStatus.storageHealth.state !== "ready") return;
    void Promise.allSettled([
      sessionActions.loadProjects(),
      sessionActions.loadSessions(),
      modelActions.load(),
      approvalActions.loadPending(),
    ]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runtimeStatus.state, runtimeStatus.state === "ready" ? runtimeStatus.storageHealth.state : null]);

  useEffect(() => {
    const snapshot = sessionState.snapshot;
    if (!snapshot || !modelState.list) return;
    if (modelSessionInitializedRef.current === snapshot.session.id) return;
    modelSessionInitializedRef.current = snapshot.session.id;
    modelActions.initialize(
      modelState.list,
      resolveSessionModelId(snapshot.runs),
    );
  }, [modelState.list, sessionState.snapshot?.session.id]);

  useEffect(() => {
    const sessionId = sessionState.snapshot?.session.id;
    if (!sessionId || runtimeStatus.state !== "ready" || !isStorageReady) return;
    void responseActionActions.load(sessionId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionState.snapshot?.session.id, runtimeStatus.state, isStorageReady]);

  useEffect(() => {
    setDockOpen(false);
    setDockExpanded(false);
    setOpenTools([]);
    setSelectedFile(undefined);
  }, [
    sessionState.snapshot?.session.id,
    sessionState.snapshot?.session.executionMode,
    sessionState.snapshot?.session.associatedWorktreeId,
  ]);

  // -----------------------------------------------------------------------
  // Runtime notifications
  // -----------------------------------------------------------------------
  useEffect(() => {
    const unsubNotifications = window.eidosRuntime.onNotification((notification) => {
      handleContextUsageNotification(notification);
      gitReviewActions.handleNotification(notification);
      if (notification.method === "session/titleUpdated") {
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

    const unsubApprovals = window.eidosRuntime.onApprovalRequest((request) => {
      approvalActions.addApproval(request);
    });

    return () => {
      unsubNotifications();
      unsubApprovals();
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
    const unsubNewTask = window.eidosRuntime.onShortcut(IPC.APP_NEW_TASK, () => {
      if (hasBlockingModal || settingsOpen) return;
      handleCreateSession();
    });
    const unsubOpenWorkspace = window.eidosRuntime.onShortcut(IPC.APP_OPEN_WORKSPACE, () => {
      if (hasBlockingModal || settingsOpen) return;
      setProjectPickerOpen(true);
    });
    return () => {
      unsubNewTask();
      unsubOpenWorkspace();
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

  // -----------------------------------------------------------------------
  // Session Selection & Creation with Model re-eval
  // -----------------------------------------------------------------------
  async function handleSelectSession(session: Session) {
    return sessionActions.selectSession(session);
  }

  function handleCreateSession(project?: Project | null): void {
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

  async function confirmHandoff(target: "local" | "worktree"): Promise<void> {
    if (!handoffSessionId) return;
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
      if (!draftInput.trim()) return;
      const materialized = await sessionActions.materializeDraft();
      if (!materialized) return;
      const started = await runActions.submitInput({
        snapshot: materialized,
        selectedModelId: modelState.selectedModelId,
        isStorageReady,
        inputOverride: draftInput,
        onRunProjected: sessionActions.projectRun,
      });
      if (started) {
        runActions.setInputForSession(draftSnapshot.session.id, "");
        sessionActions.discardDraft();
      } else {
        const rolledBack = await sessionActions.rollbackMaterializedSession(materialized.session, draftSnapshot);
        if (!rolledBack) sessionActions.setError("Run 启动结果不明确，已保留 Session 供检查。");
      }
      return;
    }
    if (!sessionState.snapshot) return;
    await runActions.submitInput({
      snapshot: sessionState.snapshot,
      selectedModelId: modelState.selectedModelId,
      isStorageReady,
      onRunProjected: sessionActions.projectRun,
    });
  }

  async function handleReviewFeedback(feedback: string): Promise<void> {
    if (!sessionState.snapshot || !modelState.selectedModelId) return;
    await runActions.submitInput({
      snapshot: sessionState.snapshot,
      selectedModelId: modelState.selectedModelId,
      isStorageReady,
      inputOverride: feedback,
      onRunProjected: sessionActions.projectRun,
    });
  }

  async function reviseLatestRun(run: Run, userInput?: string): Promise<void> {
    const snapshot = sessionState.snapshot;
    if (!snapshot || run.sessionId !== snapshot.session.id) return;
    await runActions.reviseRun({
      snapshot,
      sourceRunId: run.id,
      ...(userInput !== undefined ? { userInput } : {}),
      isStorageReady,
      onRunProjected: sessionActions.projectRun,
      onRevisionProjected: (revision) => {
        responseActionActions.projectRevision(snapshot.session.id, revision);
      },
      onRefreshSession: sessionActions.refreshCompletedSession,
    });
  }

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------
  const { composerMode, activeRun, input } = runState;
  const { snapshot } = sessionState;
  const currentSnapshot = snapshot ?? sessionState.draft;
  const isDraft = Boolean(!snapshot && sessionState.draft);
  const { approvals, respondingApprovalIds, respondingKindByApprovalId, errorsByApprovalId } = approvalState;
  const sessionWorktree = currentSnapshot?.session.worktree;
  const sessionIsLocal = currentSnapshot?.session.executionMode === "local"
    || (currentSnapshot?.session.executionMode === undefined && sessionWorktree === undefined);
  const selectedProject = currentSnapshot?.session.project?.id
    ? sessionState.projects.find((project) => project.id === currentSnapshot.session.project?.id)
    : undefined;
  const sessionProject = selectedProject ?? currentSnapshot?.session.project;
  const sessionHasProject = Boolean(!isDraft && currentSnapshot && currentSnapshot.session.projectless !== true && sessionProject);
  const sessionHasGit = !isDraft && sessionProject?.gitAvailable === true;
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
      ].join(":")
    : "empty";
  const availableTools: WorkspaceToolKind[] = sessionHasProject
    ? sessionHasGit
      ? ["review", "terminal", "files"]
      : ["terminal", "files"]
    : [];

  function openTool(tool: WorkspaceToolKind): void {
    if (!availableTools.includes(tool)) return;
    setOpenTools((current) => current.includes(tool) ? current : [...current, tool]);
    setActiveTool(tool);
    setDockOpen(true);
  }

  function toggleDock(): void {
    if (dockOpen) {
      setDockOpen(false);
      setDockExpanded(false);
      return;
    }
    const defaultTool: WorkspaceToolKind = sessionHasGit ? "review" : "files";
    if (openTools.length === 0) setOpenTools([defaultTool]);
    setActiveTool(openTools.includes(activeTool) ? activeTool : defaultTool);
    setDockOpen(true);
  }

  function closeTool(tool: WorkspaceToolKind): void {
    setOpenTools((current) => {
      const next = current.filter((item) => item !== tool);
      if (activeTool === tool && next[0]) setActiveTool(next[0]);
      if (next.length === 0) {
        setDockOpen(false);
        setDockExpanded(false);
      }
      return next;
    });
  }

  function openGitWorkflow(): void {
    openTool("review");
    setWorkflowOpenRequest((request) => request + 1);
  }

  const isRenamingThisSession = Boolean(snapshot && renamingSessionId === snapshot.session.id);

  const sidebarDisabled =
    sessionState.pending.creatingSession === true
    || handoffBusy
    || !isStorageReady;

  return (
    <main className="workbench">
      <SessionSidebar
        sessions={sessionState.sessions}
        projects={sessionState.projects}
        selectedId={sessionState.navigationSessionId ?? currentSnapshot?.session.id}
        disabled={sidebarDisabled}
        readCompletedSessions={sessionState.readCompletedSessions}
        runtimePresentation={runtimePresentation}
        isSelectingSessionId={sessionState.pending.selectingSessionId}
        gitStatusBySessionId={gitReviewState.statusBySessionId}
        onCreate={() => handleCreateSession()}
        onCreateInProject={handleCreateInProject}
        onSelect={(session) => void handleSelectSession(session)}
        onRename={(session) => void beginRename(session)}
        onDelete={(session) => requestDeleteSession(session)}
        onDeleteProject={(project) => requestDeleteProject(project)}
        onOpenSettings={() => {
          setSettingsOpen(true);
          setDockOpen(false);
          setDockExpanded(false);
          setRenamingSessionId(undefined);
        }}
      />

      <section ref={workspaceRef} className="workspace" aria-label="Agent 工作区" tabIndex={-1}>
        {/* Global Runtime error banner */}
        {runtimePresentation.tone === "warning" && runtimePresentation.description && (
          <p className="error-banner" role="alert">{runtimePresentation.description}</p>
        )}

        {/* Domain error banner */}
        {topError && <p className="error-banner" role="alert">{topError}</p>}

        {currentSnapshot?.session.worktreeRestoreAvailable === true && !isDraft && (
          <div className="worktree-restore-banner" role="status">
            <span>Worktree 已清理以释放磁盘空间</span>
            <Button
              variant="secondary"
              size="small"
              disabled={restoreBusy}
              loading={restoreBusy}
              onClick={() => void sessionActions.restoreWorktree(snapshot!.session.id)}
            >
              Restore Worktree
            </Button>
          </div>
        )}

        {settingsOpen ? (
          <SettingsPage
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
            onToggleMcp={(pId, sId, enabled) => extensionActions.setMcpEnabled(pId, sId, enabled)}
          />
        ) : currentSnapshot ? (
          <>
            <header className="workspace-header session-header">
              {isRenamingThisSession ? (
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
                          onClick: () => void beginRename(currentSnapshot.session),
                        },
                        {
                          key: "delete",
                          label: "删除任务",
                          danger: true,
                          disabled: Boolean(activeRun) || handoffBusy,
                          onClick: () => requestDeleteSession(currentSnapshot.session),
                        },
                      ]}
                    />
                  )}
                </div>
              )}
              <div className="session-header-actions">
                {sessionHasProject && (
                  <div className="workspace-header-tools">
                    <details className="environment-popover" key={executionKey}>
                      <summary className="icon-button" role="button" aria-label="环境信息">
                        <svg viewBox="0 0 20 20" aria-hidden="true">
                          <circle cx="5" cy="5" r="2" />
                          <circle cx="5" cy="10" r="2" />
                          <circle cx="5" cy="15" r="2" />
                          <path d="M9 5h7M9 10h7M9 15h7" />
                        </svg>
                      </summary>
                      <section className="environment-popover__panel" aria-label="环境信息预览">
                        <header>
                          <h2>环境信息</h2>
                        </header>
                        {sessionHasGit && (
                          <button type="button" className="environment-popover__row" onClick={() => openTool("review")}>
                            <span>变更</span>
                            <span className="git-line-summary" aria-label="修改行数">
                              <ins>+{gitReviewState.summary?.additions ?? 0}</ins>
                              <del>-{gitReviewState.summary?.deletions ?? 0}</del>
                            </span>
                          </button>
                        )}
                        <div className="environment-popover__row">
                          <span>{sessionIsLocal ? "本地" : "Worktree"}</span>
                          {sessionHasGit && (
                            <Button
                              variant="ghost"
                              size="small"
                              disabled={Boolean(activeRun) || handoffBusy}
                              loading={handoffBusy}
                              onClick={() => setHandoffSessionId(snapshot!.session.id)}
                            >
                              Hand off
                            </Button>
                          )}
                        </div>
                        {sessionHasGit && (
                          <div className="environment-popover__row environment-popover__branch">
                            <span>{sessionBranch ?? `Detached @ ${(gitReviewState.status?.head ?? "").slice(0, 7)}`}</span>
                            <span aria-hidden="true">→</span>
                            <span>{gitReviewState.summary?.compareRef ?? gitReviewState.status?.baseRef ?? "HEAD"}</span>
                          </div>
                        )}
                        {gitReviewState.summary?.statsIncomplete && (
                          <p className="environment-popover__note" role="status">二进制文件未计入行数</p>
                        )}
                        {sessionHasGit && (
                          <button type="button" className="environment-popover__row" onClick={openGitWorkflow}>
                            <span>提交或推送</span>
                            <span aria-hidden="true">›</span>
                          </button>
                        )}
                      </section>
                    </details>
                    <Button
                      variant="ghost"
                      size="small"
                      className="workspace-tools-trigger"
                      aria-label={dockOpen ? "关闭工作区工具" : "打开工作区工具"}
                      aria-expanded={dockOpen}
                      aria-controls="workspace-dock"
                      onClick={toggleDock}
                    >
                      <svg viewBox="0 0 20 20" aria-hidden="true">
                        <path d="M3 3.5h14v13H3zM12.5 3.5v13" />
                      </svg>
                    </Button>
                  </div>
                )}
              </div>
            </header>

            <div
              className={`workspace-content${dockOpen ? " workspace-content--with-dock" : ""}${dockExpanded ? " workspace-content--expanded" : ""}`}
            >
              <div className="workspace-main">
                {responseActionState.error && (
                  <p className="approval-error response-action-error" role="alert">
                    {responseActionState.error}
                  </p>
                )}

                <ExecutionFeed
                  items={currentSnapshot.items}
                  runs={currentSnapshot.runs}
                  models={modelState.list?.models ?? []}
                  responseActionState={responseActionState.responseState}
                  pendingFeedbackItemIds={responseActionState.pendingFeedbackItemIds}
                  revisionSubmitting={runState.isSubmitting}
                  stepResolutions={currentSnapshot.stepResolutions}
                  approvals={approvals.filter((a) => a.sessionId === currentSnapshot.session.id)}
                  respondingApprovalIds={respondingApprovalIds}
                  respondingKindByApprovalId={respondingKindByApprovalId}
                  expiredApprovalIds={approvalState.expiredApprovalIds}
                  errorsByApprovalId={errorsByApprovalId}
                  approvalLoadError={approvalState.pendingApprovalsLoadError}
                  loadingPendingApprovals={approvalState.loadingPendingApprovals}
                  onRetryLoadPending={() => void approvalActions.loadPending()}
                  onApprove={(request) => void approvalActions.approve(request)}
                  onReject={(request) => void approvalActions.reject(request)}
                  onFeedback={(itemId, feedback) =>
                    responseActionActions.setFeedback(currentSnapshot.session.id, itemId, feedback)}
                  onRegenerate={(run) => reviseLatestRun(run)}
                  onEditResend={(run, editedInput) => reviseLatestRun(run, editedInput)}
                />

                <Composer
                  ref={composerRef}
                  composerMode={worktreeRestoreRequired ? "read_only" : composerMode}
                  activeRun={activeRun}
                  input={input}
                  modelList={modelState.list}
                  selectedModelId={modelState.selectedModelId}
                  contextUsage={contextUsageState.usage}
                  modelConfigured={Boolean(modelState.list?.models.length)}
                  modelLoading={modelState.loading}
                  isSubmitting={runState.isSubmitting || sessionState.pending.creatingSession === true || handoffBusy || restoreBusy}
                  submitKind={runState.submitKind}
                  cancelingRunId={runState.cancelingRunId}
                  onInputChange={runActions.setInput}
                  onSubmit={handleSubmit}
                  onCancel={() => activeRun && !isDraft && snapshot && void runActions.cancelRun({ runId: activeRun.id, sessionId: snapshot.session.id })}
                  onModelChange={(id) => modelActions.selectModel(id)}
                  onOpenModelSettings={() => {
                    setSettingsOpen(true);
                    setDockOpen(false);
                    setDockExpanded(false);
                  }}
                  showSessionContext={currentSnapshot.session.taskStatus === "new"}
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
              </div>

              {dockOpen && availableTools.length > 0 && (
                <WorkspaceDock
                  activeTool={activeTool}
                  availableTools={availableTools}
                  expanded={dockExpanded}
                  openTools={openTools}
                  filesTitle={selectedFile?.split("/").pop()}
                  terminalTitle={sessionProject?.name}
                  onAddTool={openTool}
                  onClose={() => {
                    setDockOpen(false);
                    setDockExpanded(false);
                  }}
                  onCloseTool={closeTool}
                  onSelectTool={setActiveTool}
                  onToggleExpanded={() => setDockExpanded((expanded) => !expanded)}
                  review={sessionHasGit ? (
                    <GitChangesPanel
                      sessionId={currentSnapshot.session.id}
                      workspaceRoot={currentSnapshot.session.project?.workspaceRoot ?? currentSnapshot.session.workspaceRoot}
                      scope={gitReviewState.scope}
                      status={gitReviewState.status}
                      summary={gitReviewState.summary}
                      workflowOpenRequest={workflowOpenRequest}
                      loading={gitReviewState.loadingStatus || gitReviewState.loadingSummary}
                      error={gitReviewState.error}
                      onScopeChange={gitReviewActions.selectScope}
                      onRefresh={gitReviewActions.refresh}
                      onSendReviewFeedback={handleReviewFeedback}
                      reviewFeedbackDisabled={Boolean(activeRun) || runState.isSubmitting}
                      workflowDisabled={
                        Boolean(activeRun)
                        || runState.isSubmitting
                        || handoffBusy
                        || sessionState.pending.branchSessionId === currentSnapshot.session.id
                        || sessionState.pending.creatingBranchSessionId === currentSnapshot.session.id
                      }
                      onCreateBranch={
                        sessionIsLocal || (sessionWorktree?.state === "active" && sessionWorktree.branch === null)
                          ? () => openCreateBranch(currentSnapshot.session.id, sessionIsLocal ? "local" : "worktree")
                          : undefined
                      }
                    />
                  ) : null}
                  terminal={(
                    <Suspense fallback={<p className="terminal-panel__message" role="status">正在准备终端…</p>}>
                      <TerminalPanel
                        key={executionKey}
                        sessionId={currentSnapshot.session.id}
                        active={activeTool === "terminal"}
                      />
                    </Suspense>
                  )}
                  files={sessionHasProject ? (
                    <WorkspaceExplorer
                      sessionId={currentSnapshot.session.id}
                      executionKey={executionKey}
                      layout={dockExpanded ? "expanded" : "side"}
                      onSelectedFileChange={setSelectedFile}
                    />
                  ) : null}
                />
              )}
            </div>
          </>
        ) : (
          <div className="empty-state">
            <div className="empty-hero">
              <EidosMark className="empty-logo" variant="hero" />
            </div>
            <h2>让思考抵达现实</h2>
            <p className="empty-subtitle">深度工作，理解复杂问题，分析关键脉络，持续推进每一项重要任务</p>
            <div className="empty-actions">
              <PrimaryActionButton
                size="large"
                label="开始对话"
                subtitle="不选择项目也可以开始"
                showArrow={true}
                disabled={sessionState.pending.creatingSession || !isStorageReady}
                onClick={() => handleCreateSession()}
              />
              <PrimaryActionButton
                size="large"
                label="选择项目"
                subtitle="选择一个项目开始使用 Eidos"
                showArrow={true}
                disabled={sessionState.pending.creatingSession || !isStorageReady}
                onClick={() => void handleSelectProject()}
              />
            </div>
          </div>
        )}
      </section>

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
        busy={handoffBusy}
        error={sessionState.error}
        getFallbackFocus={getDialogFallbackFocus}
        onConfirm={(target) => void confirmHandoff(target)}
        onCancel={() => {
          if (handoffBusy) return;
          setHandoffSessionId(undefined);
          sessionActions.setError(undefined);
        }}
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
  );
}
