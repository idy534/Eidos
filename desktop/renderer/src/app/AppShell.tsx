import { useCallback, useEffect, useRef, useState } from "react";
import type { Session } from "../contracts.js";
import { SettingsPage } from "../components/settings/SettingsPage.js";
import { ExecutionFeed } from "../components/ExecutionFeed.js";
import { EidosMark } from "../components/EidosMark.js";
import { SessionSidebar } from "../components/SessionSidebar.js";
import { Button } from "../components/Button.js";
import { DropdownMenu } from "../components/DropdownMenu.js";
import { PrimaryActionButton } from "../components/PrimaryActionButton.js";
import { ConfirmDialog } from "../components/settings/ConfirmDialog.js";
import { Composer } from "../components/Composer.js";
import type { RuntimeLifecycleState } from "./useRuntimeLifecycle.js";
import { useSessionController } from "./useSessionController.js";
import { useRunController } from "./useRunController.js";
import { useApprovalController } from "./useApprovalController.js";
import { useModelController } from "./useModelController.js";
import { resolveSessionModelId } from "./session-model-resolver.js";
import { useExtensionController } from "./useExtensionController.js";
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

  // UI-only state (not domain state)
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [renamingSessionId, setRenamingSessionId] = useState<string | undefined>(undefined);
  const [titleDraft, setTitleDraft] = useState("");
  const [renameError, setRenameError] = useState<string | undefined>(undefined);
  const [sessionToDelete, setSessionToDelete] = useState<Session | undefined>(undefined);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | undefined>(undefined);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const modelSessionInitializedRef = useRef<string | undefined>(undefined);
  const getDialogFallbackFocus = useCallback((): HTMLElement | null => {
    const composer = composerRef.current;
    return composer?.isConnected && !composer.disabled
      ? composer
      : workspaceRef.current;
  }, []);

  // Aggregate workbench error display (domain-scoped errors remain in their respective components/pages)
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

  // -----------------------------------------------------------------------
  // Runtime notifications
  // -----------------------------------------------------------------------
  useEffect(() => {
    const unsubNotifications = window.eidosRuntime.onNotification((notification) => {
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
  }, []);

  // -----------------------------------------------------------------------
  // Keyboard shortcuts from Main process menu
  // -----------------------------------------------------------------------
  const hasBlockingModal =
    Boolean(sessionToDelete) ||
    deleteBusy ||
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
    return sessionActions.createSession(workspaceRoot);
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
  // Composer submit
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

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------
  const { composerMode, activeRun, input } = runState;
  const { snapshot } = sessionState;
  const { approvals, respondingApprovalIds, respondingKindByApprovalId, errorsByApprovalId } = approvalState;

  const isRenamingThisSession = Boolean(snapshot && renamingSessionId === snapshot.session.id);

  const sidebarDisabled =
    sessionState.pending.creatingSession === true
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
        onCreate={() => void handleCreateSession()}
        onCreateInWorkspace={(root) => void handleCreateSession(root)}
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
                        disabled: Boolean(activeRun),
                        onClick: () => requestDeleteSession(snapshot.session),
                      },
                    ]}
                  />
                </div>
              )}
            </header>

            <ExecutionFeed
              items={snapshot.items}
              runs={snapshot.runs}
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
            />

            <Composer
              ref={composerRef}
              composerMode={composerMode}
              activeRun={activeRun}
              input={input}
              modelList={modelState.list}
              selectedModelId={modelState.selectedModelId}
              modelConfigured={Boolean(modelState.list?.models.length)}
              modelLoading={modelState.loading}
              isSubmitting={runState.isSubmitting}
              submitKind={runState.submitKind}
              cancelingRunId={runState.cancelingRunId}
              onInputChange={runActions.setInput}
              onSubmit={handleSubmit}
              onCancel={() => activeRun && snapshot && void runActions.cancelRun({ runId: activeRun.id, sessionId: snapshot.session.id })}
              onModelChange={(id) => modelActions.selectModel(id)}
              onOpenModelSettings={() => setSettingsOpen(true)}
            />
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
