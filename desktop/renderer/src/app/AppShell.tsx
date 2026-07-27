import { useEffect, useRef, useState } from "react";
import type { ModelId, Run, Session } from "../contracts.js";
import { SettingsPage } from "../components/settings/SettingsPage.js";
import { ExecutionFeed } from "../components/ExecutionFeed.js";
import { EidosMark } from "../components/EidosMark.js";
import { SessionSidebar } from "../components/SessionSidebar.js";
import { Button } from "../components/Button.js";
import { DropdownMenu } from "../components/DropdownMenu.js";
import { PrimaryActionButton } from "../components/PrimaryActionButton.js";
import { ApprovalFeedbackDialog } from "../components/ApprovalFeedbackDialog.js";
import { ConfirmDialog } from "../components/settings/ConfirmDialog.js";
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
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [renameError, setRenameError] = useState<string | undefined>(undefined);
  const [sessionToDelete, setSessionToDelete] = useState<Session | undefined>(undefined);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | undefined>(undefined);

  // Aggregate error display (inline errors take priority in their domain)
  const topError = sessionState.error ?? runState.error ?? modelState.error ?? extensionState.error;

  // -----------------------------------------------------------------------
  // Bootstrap: load sessions, model, approvals independently
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (runtimeStatus.state !== "ready" || runtimeStatus.storageHealth.state !== "ready") return;
    void Promise.all([
      window.eidosRuntime.listSessions(),
      window.eidosRuntime.getModelStatus(),
      window.eidosRuntime.listModels(),
      window.eidosRuntime.listPendingApprovals(),
    ]).then(([sessionPage, modelStatus, availableModels, pendingApprovals]) => {
      sessionActions.setSessions(sessionPage.items);
      approvalActions.mergeApprovals(pendingApprovals);
      modelActions.initialize(modelStatus, availableModels, resolveSessionModelId(sessionState.snapshot?.runs ?? []));
    }).catch((cause: unknown) => {
      const msg = cause instanceof Error ? cause.message : String(cause);
      sessionActions.setError(msg);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runtimeStatus.state, runtimeStatus.state === "ready" ? runtimeStatus.storageHealth.state : null]);

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
        approvalActions.clearApprovalsForRun(notification.params.runId);
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
    Boolean(approvalState.feedbackDialogApproval) ||
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
  async function handleSelectSession(session: Session): Promise<void> {
    const loaded = await sessionActions.selectSession(session);
    if (loaded && modelState.status && modelState.list) {
      modelActions.initialize(modelState.status, modelState.list, resolveSessionModelId(loaded.runs));
    }
  }

  async function handleCreateSession(workspaceRoot?: string): Promise<void> {
    const loaded = await sessionActions.createSession(workspaceRoot);
    if (loaded && modelState.status && modelState.list) {
      modelActions.initialize(modelState.status, modelState.list, resolveSessionModelId(loaded.runs));
    }
  }

  // -----------------------------------------------------------------------
  // Rename flow
  // -----------------------------------------------------------------------
  async function beginRename(session: Session): Promise<void> {
    if (sessionState.snapshot?.session.id !== session.id) {
      await handleSelectSession(session);
    }
    setTitleDraft(session.title ?? "新任务");
    setRenameError(undefined);
    setRenaming(true);
  }

  async function submitRename(): Promise<void> {
    const sid = sessionState.snapshot?.session.id;
    if (!sid || !titleDraft.trim()) return;
    setRenameError(undefined);
    try {
      await sessionActions.renameSession(sid, titleDraft.trim());
      setRenaming(false);
    } catch (cause) {
      setRenameError(userFacingError(cause));
      // Rename mode remains open so user can retry!
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
      setDeleteError(sessionState.error || "删除任务失败，请重试。");
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
  const { approvals, respondingApprovalIds, respondingKindByApprovalId, feedbackDialogApproval, feedbackDialogError, errorsByApprovalId } = approvalState;

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
          setRenaming(false);
        }}
      />

      <section className="workspace" aria-label="Agent 工作区">
        {/* Global Runtime error banner */}
        {runtimePresentation.tone === "warning" && runtimePresentation.description && (
          <p className="error-banner" role="alert">{runtimePresentation.description}</p>
        )}

        {/* Domain error banner */}
        {topError && <p className="error-banner" role="alert">{topError}</p>}

        {settingsOpen ? (
          <SettingsPage
            runtime={runtimeStatus}
            model={modelState.status}
            modelList={modelState.list}
            modelLoading={modelState.loading}
            modelError={modelState.error}
            modelConfiguring={modelState.configuring}
            plugins={extensionState.plugins}
            skills={extensionState.skills}
            mcpServers={extensionState.mcpServers}
            extensionError={extensionState.error}
            pendingAction={extensionState.pendingAction}
            hasBlockingModal={hasBlockingModal}
            onClose={() => setSettingsOpen(false)}
            onConfigureModel={(key) => modelActions.configure(key)}
            onImportPlugin={() => extensionActions.importPlugin()}
            onTogglePlugin={(id, enabled) => extensionActions.setPluginEnabled(id, enabled)}
            onRemovePlugin={(id) => extensionActions.removePlugin(id)}
            onToggleMcp={(pId, sId, enabled) => extensionActions.setMcpEnabled(pId, sId, enabled)}
          />
        ) : snapshot ? (
          <>
            <header className="workspace-header session-header">
              {renaming ? (
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
                    onClick={() => { setRenaming(false); setRenameError(undefined); }}
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
              approvals={approvals.filter((a) => a.sessionId === snapshot.session.id)}
              respondingApprovalId={approvalState.respondingApprovalId}
              respondingApprovalIds={respondingApprovalIds}
              respondingKindByApprovalId={respondingKindByApprovalId}
              expiredApprovalIds={approvalState.expiredApprovalIds}
              errorsByApprovalId={errorsByApprovalId}
              onApprove={(request) => void approvalActions.approve(request)}
              onReject={(request) => approvalActions.openRejectDialog(request)}
            />

            <Composer
              composerMode={composerMode}
              activeRun={activeRun}
              input={input}
              modelList={modelState.list}
              selectedModelId={modelState.selectedModelId}
              modelConfigured={modelState.status?.configured ?? false}
              modelLoading={modelState.loading}
              isSubmitting={runState.isSubmitting}
              submitKind={runState.submitKind}
              hasRuns={snapshot.runs.length > 0}
              cancelingRunId={runState.cancelingRunId}
              onInputChange={runActions.setInput}
              onSubmit={handleSubmit}
              onCancel={() => activeRun && void runActions.cancelRun(activeRun.id)}
              onModelChange={(id) => modelActions.selectModel(id)}
            />
          </>
        ) : (
          <div className="empty-state">
            <div className="empty-hero">
              <EidosMark className="empty-logo" variant="hero" />
            </div>
            <h2>让想法拥有可执行的形态</h2>
            <p className="empty-subtitle">面向未来的 Agent Runtime 桌面端，安全读取、分析与演进代码库</p>
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
        onConfirm={() => void confirmDelete()}
        onCancel={() => { setSessionToDelete(undefined); setDeleteError(undefined); }}
      />

      {/* Approval reject feedback dialog */}
      <ApprovalFeedbackDialog
        approval={feedbackDialogApproval}
        busy={Boolean(feedbackDialogApproval && respondingApprovalIds.has(feedbackDialogApproval.id))}
        error={feedbackDialogError}
        onConfirm={(request, feedback) => void approvalActions.submitReject(request, feedback)}
        onCancel={() => approvalActions.closeFeedbackDialog()}
      />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Composer — exported for direct behavior testing
// ---------------------------------------------------------------------------

export interface ComposerProps {
  composerMode: import("../session-state.js").ComposerMode;
  activeRun: Run | undefined;
  input: string;
  modelList: import("../contracts.js").ModelListResult | undefined;
  selectedModelId: ModelId | undefined;
  modelConfigured: boolean;
  modelLoading: boolean;
  isSubmitting: boolean;
  submitKind: "start" | "continue" | undefined;
  hasRuns: boolean;
  cancelingRunId: string | undefined;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onModelChange: (id: ModelId) => void;
}

export function Composer({
  composerMode,
  activeRun,
  input,
  modelList,
  selectedModelId,
  modelConfigured,
  modelLoading,
  isSubmitting,
  submitKind,
  hasRuns,
  cancelingRunId,
  onInputChange,
  onSubmit,
  onCancel,
  onModelChange,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const minHeight = 56;
    const maxHeight = 168;
    const targetHeight = Math.min(Math.max(el.scrollHeight, minHeight), maxHeight);
    el.style.height = `${targetHeight}px`;
  }, [input]);

  const isReadOnly = composerMode === "read_only";
  const isIdle = composerMode === "idle";
  const isContinuing = composerMode === "waiting_user_input";
  const canCancel = (composerMode === "running" || composerMode === "starting") && activeRun?.allowedActions?.includes("cancel");
  const inputDisabled = modelLoading || isSubmitting || !modelConfigured || !selectedModelId || isReadOnly || composerMode === "finalizing" || composerMode === "waiting_approval";

  const placeholder = modelLoading
    ? "正在加载模型配置…"
    : isReadOnly
      ? "存储只读，暂无法启动 Run"
      : isContinuing
        ? "补充信息后继续这个 Run"
        : modelConfigured
          ? "例如：阅读这个项目并说明如何启动"
          : "请先配置 DeepSeek API Key";

  const showModelSelect = isIdle && !hasRuns;
  const statusLabel = modelLoading
    ? "正在加载模型…"
    : submitKind === "continue"
      ? "继续中…"
      : composerMode === "running" || composerMode === "starting"
        ? statusText(activeRun?.status ?? "queued")
        : isContinuing
          ? "等待你的补充"
          : composerMode === "waiting_approval"
            ? "等待批准"
            : composerMode === "finalizing"
              ? "正在收尾"
              : selectedModelId ?? "无可用模型";

  const buttonLabel = modelLoading
    ? "加载中…"
    : submitKind === "continue"
      ? "继续中…"
      : submitKind === "start" || composerMode === "starting"
        ? "启动中…"
        : isContinuing
          ? "继续"
          : "开始";

  const isSubmitDisabled =
    modelLoading
    || isSubmitting
    || composerMode === "starting"
    || composerMode === "running"
    || composerMode === "finalizing"
    || composerMode === "waiting_approval"
    || composerMode === "read_only"
    || !modelConfigured
    || !selectedModelId
    || !input.trim();

  return (
    <form
      className="composer"
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
    >
      <label className="sr-only" htmlFor="task-input">告诉 Eidos 要做什么</label>
      <textarea
        ref={textareaRef}
        id="task-input"
        rows={2}
        placeholder={placeholder}
        value={input}
        disabled={inputDisabled}
        onChange={(e) => onInputChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
      <div className="composer-actions">
        <div className="composer-meta">
          {showModelSelect ? (
            <>
              <label htmlFor="run-model">本次模型</label>
              <select
                id="run-model"
                value={selectedModelId ?? ""}
                disabled={composerMode !== "idle" || modelLoading || isSubmitting}
                onChange={(e) => onModelChange(e.target.value as ModelId)}
              >
                {modelList?.models.map((option) => (
                  <option key={option.id} value={option.id} disabled={!option.selectable}>
                    {option.displayName}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <span>{statusLabel}</span>
          )}
        </div>

        {canCancel ? (
          <Button
            type="button"
            variant="primary"
            size="medium"
            className="composer-submit-btn composer-cancel-btn"
            disabled={Boolean(cancelingRunId)}
            loading={Boolean(cancelingRunId)}
            onClick={onCancel}
            aria-label={cancelingRunId ? "取消中…" : "取消 Run"}
            title={cancelingRunId ? "取消中…" : "取消 Run"}
            icon={!cancelingRunId ? <StopSquareIcon /> : undefined}
          >
            <span className="sr-only">{cancelingRunId ? "取消中…" : "取消 Run"}</span>
          </Button>
        ) : (
          <Button
            type="submit"
            variant="primary"
            size="medium"
            className={`composer-submit-btn${!input.trim() ? " composer-submit-btn--empty" : ""}`}
            disabled={isSubmitDisabled}
            loading={isSubmitting || composerMode === "starting"}
            aria-label={buttonLabel}
            title={buttonLabel}
            icon={
              !isSubmitting && composerMode !== "starting" ? (
                <UpArrowIcon />
              ) : undefined
            }
          >
            <span className="sr-only">{buttonLabel}</span>
          </Button>
        )}
      </div>
    </form>
  );
}

function UpArrowIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <path
        d="M8 13.5V2.5M3.5 7L8 2.5L12.5 7"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function StopSquareIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <rect x="3" y="3" width="10" height="10" rx="1.8" fill="currentColor" />
    </svg>
  );
}

function statusText(status: Run["status"]): string {
  return ({
    queued: "已排队", running: "正在执行", waiting_approval: "等待批准",
    waiting_user_input: "等待输入", finalizing: "正在收尾", stopped: "已停止",
    succeeded: "已完成", failed: "失败", canceled: "已取消", interrupted: "已中断",
  } as const)[status];
}
