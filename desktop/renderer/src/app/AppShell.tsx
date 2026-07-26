import { useEffect, useState } from "react";
import type { ModelId, PluginRecord, Run, SkillMetadata, McpServerRecord, Session } from "../contracts.js";
import type { SettingsPendingAction } from "../components/settings/settings-types.js";
import { SettingsPage } from "../components/settings/SettingsPage.js";
import { ExecutionFeed } from "../components/ExecutionFeed.js";
import { EidosMark } from "../components/EidosMark.js";
import { SessionSidebar } from "../components/SessionSidebar.js";
import { PrimaryActionButton } from "../components/PrimaryActionButton.js";
import { ApprovalFeedbackDialog } from "../components/ApprovalFeedbackDialog.js";
import { ConfirmDialog } from "../components/settings/ConfirmDialog.js";
import type { RuntimeLifecycleState } from "./useRuntimeLifecycle.js";
import { useSessionController } from "./useSessionController.js";
import { useRunController } from "./useRunController.js";
import { useApprovalController } from "./useApprovalController.js";
import { useModelController } from "./useModelController.js";
import { applyNotification } from "../session-state.js";

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

  // UI-only state (not domain state)
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  const [plugins, setPlugins] = useState<PluginRecord[]>([]);
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const [settingsPendingAction, setSettingsPendingAction] = useState<SettingsPendingAction>(undefined);
  const [sessionToDelete, setSessionToDelete] = useState<Session | undefined>(undefined);
  const [deleteBusy, setDeleteBusy] = useState(false);

  // Aggregate error display (inline errors take priority in their domain)
  const topError = sessionState.error ?? runState.error;

  // -----------------------------------------------------------------------
  // Bootstrap: load sessions, model, approvals when runtime is ready
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
      modelActions.initialize(modelStatus, availableModels, sessionState.snapshot?.runs[0]?.modelId);
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
      if (
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
  useEffect(() => {
    const unsubNewTask = window.eidosRuntime.onShortcut("app:new-task", () => {
      if (settingsOpen || document.querySelector(".modal-backdrop")) return;
      void handleCreateSession();
    });
    const unsubOpenWorkspace = window.eidosRuntime.onShortcut("app:open-workspace", () => {
      if (document.querySelector(".modal-backdrop")) return;
      void handleCreateSession();
    });
    return () => {
      unsubNewTask();
      unsubOpenWorkspace();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settingsOpen, sessionActions.createSession]);

  // -----------------------------------------------------------------------
  // Extension refresh when settings open
  // -----------------------------------------------------------------------
  useEffect(() => {
    if (!settingsOpen || runtimeStatus.state !== "ready" || !isStorageReady) return;
    void (async () => {
      try {
        let snap = await window.eidosRuntime.readExtensions();
        const events = await window.eidosRuntime.readExtensionEvents(snap.throughEventId);
        if (events.items.length > 0) snap = await window.eidosRuntime.readExtensions();
        setPlugins(snap.plugins);
        setSkills(snap.skills);
        setMcpServers(snap.servers);
      } catch (cause: unknown) {
        console.error("[extensions]", cause);
      }
    })();
  }, [settingsOpen, runtimeStatus.state, isStorageReady]);

  // -----------------------------------------------------------------------
  // Session Selection & Creation with Model re-eval
  // -----------------------------------------------------------------------
  async function handleSelectSession(session: Session): Promise<void> {
    const loaded = await sessionActions.selectSession(session);
    if (loaded && modelState.status && modelState.list) {
      modelActions.initialize(modelState.status, modelState.list, loaded.runs[0]?.modelId);
    }
  }

  async function handleCreateSession(workspaceRoot?: string): Promise<void> {
    const loaded = await sessionActions.createSession(workspaceRoot);
    if (loaded && modelState.status && modelState.list) {
      modelActions.initialize(modelState.status, modelState.list, loaded.runs[0]?.modelId);
    }
  }

  // -----------------------------------------------------------------------
  // Settings operations
  // -----------------------------------------------------------------------
  async function importPlugin(): Promise<void> {
    setSettingsPendingAction({ type: "import_plugin" });
    try {
      const imported = await window.eidosRuntime.importPlugin();
      if (imported) {
        let snap = await window.eidosRuntime.readExtensions();
        const events = await window.eidosRuntime.readExtensionEvents(snap.throughEventId);
        if (events.items.length > 0) snap = await window.eidosRuntime.readExtensions();
        setPlugins(snap.plugins);
        setSkills(snap.skills);
        setMcpServers(snap.servers);
      }
    } catch (cause) {
      throw new Error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSettingsPendingAction(undefined);
    }
  }

  async function setPluginEnabled(pluginId: string, enabled: boolean): Promise<void> {
    setSettingsPendingAction({ type: "toggle_plugin", pluginId });
    try {
      await window.eidosRuntime.setPluginEnabled(pluginId, enabled);
      let snap = await window.eidosRuntime.readExtensions();
      const events = await window.eidosRuntime.readExtensionEvents(snap.throughEventId);
      if (events.items.length > 0) snap = await window.eidosRuntime.readExtensions();
      setPlugins(snap.plugins);
      setSkills(snap.skills);
      setMcpServers(snap.servers);
    } catch (cause) {
      throw new Error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSettingsPendingAction(undefined);
    }
  }

  async function removePlugin(pluginId: string): Promise<void> {
    setSettingsPendingAction({ type: "remove_plugin", pluginId });
    try {
      await window.eidosRuntime.removePlugin(pluginId);
      let snap = await window.eidosRuntime.readExtensions();
      const events = await window.eidosRuntime.readExtensionEvents(snap.throughEventId);
      if (events.items.length > 0) snap = await window.eidosRuntime.readExtensions();
      setPlugins(snap.plugins);
      setSkills(snap.skills);
      setMcpServers(snap.servers);
    } catch (cause) {
      throw new Error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSettingsPendingAction(undefined);
    }
  }

  async function setMcpEnabled(pluginId: string, serverId: string, enabled: boolean): Promise<void> {
    setSettingsPendingAction({ type: "toggle_mcp", pluginId, serverId });
    try {
      await window.eidosRuntime.setMcpEnabled(pluginId, serverId, enabled);
      let snap = await window.eidosRuntime.readExtensions();
      const events = await window.eidosRuntime.readExtensionEvents(snap.throughEventId);
      if (events.items.length > 0) snap = await window.eidosRuntime.readExtensions();
      setPlugins(snap.plugins);
      setSkills(snap.skills);
      setMcpServers(snap.servers);
    } catch (cause) {
      throw new Error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSettingsPendingAction(undefined);
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
    setRenaming(true);
    setSessionMenuOpen(false);
  }

  async function submitRename(): Promise<void> {
    const sid = sessionState.snapshot?.session.id;
    if (!sid || !titleDraft.trim()) return;
    try {
      await sessionActions.renameSession(sid, titleDraft.trim());
      setRenaming(false);
      setSessionMenuOpen(false);
    } catch {
      // error surfaced via sessionState.error
    }
  }

  // -----------------------------------------------------------------------
  // Delete flow
  // -----------------------------------------------------------------------
  function requestDeleteSession(session: Session): void {
    setSessionToDelete(session);
    setSessionMenuOpen(false);
  }

  async function confirmDelete(): Promise<void> {
    if (!sessionToDelete) return;
    setDeleteBusy(true);
    try {
      await sessionActions.deleteSession(sessionToDelete);
      setSessionToDelete(undefined);
    } finally {
      setDeleteBusy(false);
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
  const { approvals, respondingApprovalId, feedbackDialogApproval, feedbackDialogError } = approvalState;

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
          setSessionMenuOpen(false);
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
            plugins={plugins}
            skills={skills}
            mcpServers={mcpServers}
            pendingAction={settingsPendingAction}
            onClose={() => setSettingsOpen(false)}
            onConfigureModel={async (key) => {
              await modelActions.configure(key);
            }}
            onImportPlugin={importPlugin}
            onTogglePlugin={setPluginEnabled}
            onRemovePlugin={removePlugin}
            onToggleMcp={setMcpEnabled}
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
                  <button
                    type="submit"
                    className="btn btn--primary btn--small"
                    disabled={
                      sessionState.pending.renamingSessionId === snapshot.session.id
                      || !titleDraft.trim()
                    }
                  >
                    保存
                  </button>
                  <button
                    className="btn btn--ghost btn--small"
                    type="button"
                    onClick={() => setRenaming(false)}
                  >
                    取消
                  </button>
                </form>
              ) : (
                <div className="session-title-group">
                  <h1
                    onContextMenu={(e) => { e.preventDefault(); setSessionMenuOpen(true); }}
                  >
                    {snapshot.session.title ?? "新任务"}
                  </h1>
                  <div className="session-menu">
                    <button
                      className="icon-button"
                      aria-label="任务菜单"
                      aria-expanded={sessionMenuOpen}
                      aria-haspopup="menu"
                      onClick={() => setSessionMenuOpen((open) => !open)}
                    >
                      •••
                    </button>
                    {sessionMenuOpen && (
                      <div
                        className="session-menu-popover"
                        role="menu"
                        onKeyDown={(e) => {
                          if (e.key === "Escape") setSessionMenuOpen(false);
                        }}
                      >
                        <button
                          role="menuitem"
                          onClick={() => void beginRename(snapshot.session)}
                        >
                          编辑标题
                        </button>
                        <button
                          role="menuitem"
                          className="danger-action"
                          disabled={Boolean(activeRun)}
                          onClick={() => requestDeleteSession(snapshot.session)}
                        >
                          删除任务
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </header>

            <ExecutionFeed
              items={snapshot.items}
              runs={snapshot.runs}
              approvals={approvals.filter((a) => a.sessionId === snapshot.session.id)}
              respondingApprovalId={respondingApprovalId}
              onApprove={(request) => void approvalActions.approve(request)}
              onReject={(request) => approvalActions.openRejectDialog(request)}
            />

            <Composer
              composerMode={composerMode}
              activeRun={activeRun}
              input={input}
              modelList={modelState.list}
              selectedModelId={modelState.selectedModelId ?? "deepseek-v4-flash"}
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
        onConfirm={() => void confirmDelete()}
        onCancel={() => setSessionToDelete(undefined)}
      />

      {/* Approval reject feedback dialog */}
      <ApprovalFeedbackDialog
        approval={feedbackDialogApproval}
        busy={Boolean(respondingApprovalId && feedbackDialogApproval && respondingApprovalId === feedbackDialogApproval.id)}
        error={feedbackDialogError}
        onConfirm={(request, feedback) => void approvalActions.submitReject(request, feedback)}
        onCancel={() => approvalActions.closeFeedbackDialog()}
      />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Composer — extracted to avoid crowding AppShell render
// ---------------------------------------------------------------------------

interface ComposerProps {
  composerMode: import("../session-state.js").ComposerMode;
  activeRun: Run | undefined;
  input: string;
  modelList: import("../contracts.js").ModelListResult | undefined;
  selectedModelId: ModelId;
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

function Composer({
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
  const isReadOnly = composerMode === "read_only";
  const isIdle = composerMode === "idle";
  const isContinuing = composerMode === "waiting_user_input";
  const canCancel = (composerMode === "running" || composerMode === "starting") && activeRun?.allowedActions?.includes("cancel");
  const inputDisabled = modelLoading || isSubmitting || !modelConfigured || isReadOnly || composerMode === "finalizing" || composerMode === "waiting_approval";

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
              : selectedModelId;

  const buttonLabel = modelLoading
    ? "加载中…"
    : submitKind === "continue"
      ? "继续中…"
      : submitKind === "start" || composerMode === "starting"
        ? "启动中…"
        : isContinuing
          ? "继续"
          : "开始";

  return (
    <form
      className="composer"
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
    >
      <label className="sr-only" htmlFor="task-input">告诉 Eidos 要做什么</label>
      <textarea
        id="task-input"
        rows={2}
        placeholder={placeholder}
        value={input}
        disabled={inputDisabled}
        onChange={(e) => onInputChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
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
                value={selectedModelId}
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
          <button
            type="button"
            className="btn btn--ghost btn--medium"
            disabled={Boolean(cancelingRunId)}
            aria-busy={Boolean(cancelingRunId)}
            onClick={onCancel}
          >
            {cancelingRunId ? "取消中…" : "取消 Run"}
          </button>
        ) : (
          <button
            type="submit"
            className="btn btn--primary btn--medium"
            disabled={
              modelLoading
              || isSubmitting
              || composerMode === "starting"
              || composerMode === "running"
              || composerMode === "finalizing"
              || composerMode === "waiting_approval"
              || composerMode === "read_only"
              || !modelConfigured
              || !input.trim()
            }
            aria-busy={isSubmitting || composerMode === "starting"}
          >
            {buttonLabel}
          </button>
        )}
      </div>
    </form>
  );
}

function statusText(status: Run["status"]): string {
  return ({
    queued: "已排队", running: "正在执行", waiting_approval: "等待批准",
    waiting_user_input: "等待输入", finalizing: "正在收尾", stopped: "已停止",
    succeeded: "已完成", failed: "失败", canceled: "已取消", interrupted: "已中断",
  } as const)[status];
}
