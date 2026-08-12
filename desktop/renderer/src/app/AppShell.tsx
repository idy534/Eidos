import { useCallback, useEffect, useRef, useState } from "react";
import type { ProjectGitContext, Run, Session } from "../contracts.js";
import { SettingsPage } from "../components/settings/SettingsPage.js";
import { ExecutionFeed } from "../components/ExecutionFeed.js";
import { EidosMark } from "../components/EidosMark.js";
import { SessionSidebar } from "../components/SessionSidebar.js";
import { Button } from "../components/Button.js";
import { DropdownMenu } from "../components/DropdownMenu.js";
import { PrimaryActionButton } from "../components/PrimaryActionButton.js";
import { ConfirmDialog } from "../components/settings/ConfirmDialog.js";
import { CreateSessionDialog } from "../components/CreateSessionDialog.js";
import { CreateBranchDialog } from "../components/CreateBranchDialog.js";
import { HandoffDialog } from "../components/HandoffDialog.js";
import { Composer } from "../components/Composer.js";
import { GitChangesPanel } from "../components/GitChangesPanel.js";
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
  const [runState, runActions] = useRunController(sessionState.snapshot, isStorageReady);
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
  const [createSessionDraft, setCreateSessionDraft] = useState<{
    workspaceRoot: string;
    gitContext: ProjectGitContext;
  } | undefined>(undefined);
  const [createSessionContextBusy, setCreateSessionContextBusy] = useState(false);
  const [createSessionConfirmBusy, setCreateSessionConfirmBusy] = useState(false);
  const [createBranchSessionId, setCreateBranchSessionId] = useState<string | undefined>(undefined);
  const [handoffSessionId, setHandoffSessionId] = useState<string | undefined>(undefined);
  const [contentView, setContentView] = useState<"conversation" | "changes">("conversation");
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
    setContentView("conversation");
  }, [sessionState.snapshot?.session.id]);

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
    Boolean(createSessionDraft) ||
    createSessionContextBusy ||
    createSessionConfirmBusy ||
    Boolean(createBranchSessionId) ||
    Boolean(handoffSessionId) ||
    sessionState.pending.creatingBranchSessionId !== undefined ||
    sessionState.pending.handoffSessionId !== undefined ||
    sessionState.pending.creatingSession === true;

  useEffect(() => {
    const unsubNewTask = window.eidosRuntime.onShortcut(IPC.APP_NEW_TASK, () => {
      if (hasBlockingModal || settingsOpen) return;
      void handleCreateSession();
    });
    const unsubOpenWorkspace = window.eidosRuntime.onShortcut(IPC.APP_OPEN_WORKSPACE, () => {
      if (hasBlockingModal || settingsOpen) return;
      void handleCreateSession();
    });
    return () => {
      unsubNewTask();
      unsubOpenWorkspace();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasBlockingModal, settingsOpen, sessionActions.createSession]);

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

  async function handleCreateSession(workspaceRoot?: string) {
    setCreateSessionContextBusy(true);
    sessionActions.setError(undefined);
    try {
      const workspace = workspaceRoot ?? await window.eidosRuntime.selectWorkspace();
      if (!workspace) return;
      const gitContext = await window.eidosRuntime.readProjectGitContext(workspace);
      setCreateSessionDraft({ workspaceRoot: workspace, gitContext });
    } catch (cause) {
      sessionActions.setError(userFacingError(cause));
    } finally {
      setCreateSessionContextBusy(false);
    }
  }

  async function confirmCreateSession(
    executionMode: "local" | "worktree",
    baseRef?: string,
    includeLocalChanges = false,
  ): Promise<void> {
    if (!createSessionDraft) return;
    setCreateSessionConfirmBusy(true);
    try {
      const created = await sessionActions.createSession(createSessionDraft.workspaceRoot, {
        executionMode,
        ...(executionMode === "worktree" && baseRef ? { baseRef } : {}),
        ...(executionMode === "worktree" ? { includeLocalChanges } : {}),
      });
      if (created) {
        setCreateSessionDraft(undefined);
      }
    } finally {
      setCreateSessionConfirmBusy(false);
    }
  }

  async function confirmCreateBranch(branch: string): Promise<void> {
    if (!createBranchSessionId) return;
    const result = await sessionActions.createSessionBranch(createBranchSessionId, branch);
    if (result) setCreateBranchSessionId(undefined);
  }

  async function confirmHandoff(target: "local" | "worktree"): Promise<void> {
    if (!handoffSessionId) return;
    const loaded = await sessionActions.handoffSession(handoffSessionId, target);
    if (loaded) setHandoffSessionId(undefined);
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
    setTitleDraft(targetSnapshot.session.title ?? "新任务");
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

  // -----------------------------------------------------------------------
  // Run submission and revision
  // -----------------------------------------------------------------------
  async function handleSubmit(): Promise<void> {
    if (!sessionState.snapshot || !modelState.selectedModelId) return;
    await runActions.submitInput({
      snapshot: sessionState.snapshot,
      selectedModelId: modelState.selectedModelId,
      isStorageReady,
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
  const { approvals, respondingApprovalIds, respondingKindByApprovalId, errorsByApprovalId } = approvalState;
  const sessionWorktree = snapshot?.session.worktree;
  const sessionHasGit = snapshot?.session.project?.gitAvailable === true;
  const handoffBusy = sessionState.pending.handoffSessionId === snapshot?.session.id;
  const restoreBusy = sessionState.pending.restoringWorktreeSessionId === snapshot?.session.id;
  const worktreeRestoreRequired = snapshot?.session.executionMode === "worktree"
    && snapshot.session.worktreeRestoreAvailable === true;

  const isRenamingThisSession = Boolean(snapshot && renamingSessionId === snapshot.session.id);

  const sidebarDisabled =
    sessionState.pending.creatingSession === true
    || createSessionContextBusy
    || createSessionConfirmBusy
    || Boolean(createSessionDraft)
    || handoffBusy
    || !isStorageReady;

  return (
    <main className="workbench">
      <SessionSidebar
        sessions={sessionState.sessions}
        selectedId={sessionState.navigationSessionId ?? snapshot?.session.id}
        disabled={sidebarDisabled}
        readCompletedSessions={sessionState.readCompletedSessions}
        runtimePresentation={runtimePresentation}
        isSelectingSessionId={sessionState.pending.selectingSessionId}
        gitStatusBySessionId={gitReviewState.statusBySessionId}
        onCreate={() => void handleCreateSession()}
        onCreateInProject={(root) => void handleCreateSession(root)}
        onSelect={(session) => void handleSelectSession(session)}
        onRename={(session) => void beginRename(session)}
        onDelete={(session) => requestDeleteSession(session)}
        onOpenSettings={() => {
          setSettingsOpen(true);
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

        {snapshot?.session.worktreeRestoreAvailable === true && (
          <div className="worktree-restore-banner" role="status">
            <span>Worktree 已清理以释放磁盘空间</span>
            <Button
              variant="secondary"
              size="small"
              disabled={restoreBusy}
              loading={restoreBusy}
              onClick={() => void sessionActions.restoreWorktree(snapshot.session.id)}
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
        ) : snapshot ? (
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
                      sessionState.pending.renamingSessionId === snapshot.session.id
                      || !titleDraft.trim()
                    }
                    loading={sessionState.pending.renamingSessionId === snapshot.session.id}
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
                  <h1>{snapshot.session.title ?? "新任务"}</h1>
                  <DropdownMenu
                    trigger="•••"
                    label="任务菜单"
                    items={[
                      {
                        key: "rename",
                        label: "编辑标题",
                        onClick: () => void beginRename(snapshot.session),
                      },
                      {
                        key: "delete",
                        label: "删除任务",
                        danger: true,
                        disabled: Boolean(activeRun) || handoffBusy,
                        onClick: () => requestDeleteSession(snapshot.session),
                      },
                    ]}
                  />
                </div>
              )}
              {sessionHasGit && (
                <div className="session-header-actions">
                  <div className="session-git-summary" aria-label="当前 Git 状态">
                    <span>
                      {gitReviewState.status?.branch
                        ?? sessionWorktree?.branch
                        ?? `Detached @ ${(gitReviewState.status?.head ?? sessionWorktree?.baseCommit ?? "").slice(0, 7)}`}
                    </span>
                    {sessionWorktree?.state === "active" && sessionWorktree.branch === null && (
                      <Button
                        variant="ghost"
                        size="small"
                        disabled={
                          handoffBusy
                          || sessionState.pending.creatingBranchSessionId === snapshot.session.id
                        }
                        onClick={() => {
                          sessionActions.setError(undefined);
                          setCreateBranchSessionId(snapshot.session.id);
                        }}
                      >
                        Create Branch
                      </Button>
                    )}
                    {gitReviewState.status && (
                      <>
                        <code>{gitReviewState.status.head.slice(0, 7)}</code>
                        <span>{gitReviewState.status.dirty ? "有改动" : "干净"}</span>
                      </>
                    )}
                    <Button
                      variant="secondary"
                      size="small"
                      disabled={Boolean(activeRun) || handoffBusy}
                      loading={handoffBusy}
                      onClick={() => setHandoffSessionId(snapshot.session.id)}
                    >
                      Hand off
                    </Button>
                  </div>
                  <div className="workspace-view-switch" aria-label="工作区视图">
                    <button
                      type="button"
                      aria-pressed={contentView === "conversation"}
                      onClick={() => setContentView("conversation")}
                    >
                      对话
                    </button>
                    <button
                      type="button"
                      aria-pressed={contentView === "changes"}
                      onClick={() => setContentView("changes")}
                    >
                      Changes
                    </button>
                  </div>
                </div>
              )}
            </header>

            {contentView === "changes" && sessionHasGit ? (
              <GitChangesPanel
                scope={gitReviewState.scope}
                status={gitReviewState.status}
                diff={gitReviewState.diff}
                loading={gitReviewState.loadingStatus || gitReviewState.loadingDiff}
                error={gitReviewState.error}
                onScopeChange={gitReviewActions.selectScope}
                onRefresh={gitReviewActions.refresh}
              />
            ) : (
              <>
                {responseActionState.error && (
                  <p className="approval-error response-action-error" role="alert">
                    {responseActionState.error}
                  </p>
                )}

                <ExecutionFeed
                  items={snapshot.items}
                  runs={snapshot.runs}
                  models={modelState.list?.models ?? []}
                  responseActionState={responseActionState.responseState}
                  pendingFeedbackItemIds={responseActionState.pendingFeedbackItemIds}
                  revisionSubmitting={runState.isSubmitting}
                  stepResolutions={snapshot.stepResolutions}
                  approvals={approvals.filter((a) => a.sessionId === snapshot.session.id)}
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
                    responseActionActions.setFeedback(snapshot.session.id, itemId, feedback)}
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
                  isSubmitting={runState.isSubmitting || handoffBusy || restoreBusy}
                  submitKind={runState.submitKind}
                  cancelingRunId={runState.cancelingRunId}
                  onInputChange={runActions.setInput}
                  onSubmit={handleSubmit}
                  onCancel={() => activeRun && snapshot && void runActions.cancelRun({ runId: activeRun.id, sessionId: snapshot.session.id })}
                  onModelChange={(id) => modelActions.selectModel(id)}
                  onOpenModelSettings={() => setSettingsOpen(true)}
                />
              </>
            )}
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
                label="选择工作空间目录"
                subtitle="打开一个本地项目开始使用 Eidos"
                showArrow={true}
                disabled={sessionState.pending.creatingSession || !isStorageReady}
                onClick={() => void handleCreateSession()}
              />
            </div>
          </div>
        )}
      </section>

      {/* Delete confirm dialog */}
      <CreateSessionDialog
        open={Boolean(createSessionDraft)}
        workspaceRoot={createSessionDraft?.workspaceRoot ?? ""}
        gitContext={createSessionDraft?.gitContext ?? {
          gitAvailable: false,
          currentBranch: null,
          head: null,
          branches: [],
          dirty: false,
          changedFileCount: 0,
        }}
        busy={createSessionConfirmBusy}
        error={sessionState.error}
        getFallbackFocus={getDialogFallbackFocus}
        onConfirm={(executionMode, baseRef, includeLocalChanges) => (
          void confirmCreateSession(executionMode, baseRef, includeLocalChanges)
        )}
        onCancel={() => {
          setCreateSessionDraft(undefined);
          sessionActions.setError(undefined);
        }}
      />
      <CreateBranchDialog
        open={Boolean(createBranchSessionId)}
        busy={sessionState.pending.creatingBranchSessionId !== undefined}
        error={sessionState.error}
        getFallbackFocus={getDialogFallbackFocus}
        onConfirm={(branch) => void confirmCreateBranch(branch)}
        onCancel={() => {
          setCreateBranchSessionId(undefined);
          sessionActions.setError(undefined);
        }}
      />
      <HandoffDialog
        open={Boolean(handoffSessionId)}
        currentMode={snapshot?.session.executionMode ?? "local"}
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
        title={`删除任务"${sessionToDelete?.title ?? "新任务"}"？`}
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
    </main>
  );
}
