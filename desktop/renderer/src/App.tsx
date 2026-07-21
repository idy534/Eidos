import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ApprovalRequest,
  ModelId,
  ModelListResult,
  ModelStatus,
  McpServerRecord,
  PluginRecord,
  Run,
  RuntimeStatus,
  Session,
  SessionSnapshot,
  SkillMetadata,
} from "./contracts";
import { ExecutionFeed } from "./components/ExecutionFeed";
import { EidosMark } from "./components/EidosMark";
import { SessionSidebar } from "./components/SessionSidebar";
import {
  applyNotification,
  SnapshotReadCoordinator,
  taskStatusFromRun,
  userFacingError,
} from "./session-state";

const READ_COMPLETIONS_KEY = "eidos.readCompletedSessionIds";


export function App() {
  const [runtime, setRuntime] = useState<RuntimeStatus>({ state: "starting" });
  const [model, setModel] = useState<ModelStatus>();
  const [modelList, setModelList] = useState<ModelListResult>();
  const [selectedModelId, setSelectedModelId] = useState<ModelId>("deepseek-v4-flash");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [navigationSessionId, setNavigationSessionId] = useState<string>();
  const [readCompletedSessions, setReadCompletedSessions] = useState<Set<string>>(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(READ_COMPLETIONS_KEY) ?? "[]");
      return new Set(Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : []);
    } catch {
      return new Set();
    }
  });
  const [snapshot, setSnapshot] = useState<SessionSnapshot>();
  const [input, setInput] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [plugins, setPlugins] = useState<PluginRecord[]>([]);
  const [skills, setSkills] = useState<SkillMetadata[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerRecord[]>([]);
  const snapshotReads = useRef(new SnapshotReadCoordinator()).current;
  const selectedSessionId = useRef<string | undefined>(undefined);

  const activeRun = useMemo(
    () => [...(snapshot?.runs ?? [])].reverse().find((run) =>
      ["queued", "running", "waiting_approval", "waiting_user_input", "finalizing"].includes(run.status)),
    [snapshot],
  );
  const continuingRun = activeRun?.status === "waiting_user_input"
    && activeRun.allowedActions?.includes("continue") ? activeRun : undefined;
  const interactionBusy = busy;

  useEffect(() => {
    const unsubscribeStatus = window.eidosRuntime.onStatus(setRuntime);
    const unsubscribeNotifications = window.eidosRuntime.onNotification((notification) => {
      if (
        notification.method === "run/started"
        || notification.method === "run/updated"
        || notification.method === "run/completed"
      ) {
        const run = notification.params.run;
        setSessions((current) => current.map((session) => session.id === run.sessionId
          ? { ...session, taskStatus: taskStatusFromRun(run), updatedAt: run.updatedAt }
          : session));
        setReadCompletedSessions((current) => {
          const next = new Set(current);
          if (run.status === "succeeded" && selectedSessionId.current === run.sessionId) {
            next.add(run.sessionId);
          } else if (["queued", "running", "waiting_approval", "waiting_user_input", "finalizing", "succeeded"].includes(run.status)) {
            next.delete(run.sessionId);
          }
          return next;
        });
      }
      if (notification.method === "run/completed") {
        setApprovals((current) => current.filter(
          (approval) => approval.runId !== notification.params.run.id,
        ));
        void refreshCompletedSession(notification.params.sessionId);
      }
      setSnapshot((current) => applyNotification(current, notification));
    });
    const unsubscribeApprovals = window.eidosRuntime.onApprovalRequest((request) => {
      setApprovals((current) => [...current.filter((item) => item.id !== request.id), request]);
    });
    void window.eidosRuntime.getStatus().then(setRuntime).catch((cause: unknown) => {
      setRuntime({ state: "error", message: messageFrom(cause) });
    });
    return () => {
      unsubscribeStatus();
      unsubscribeNotifications();
      unsubscribeApprovals();
    };
  }, []);

  useEffect(() => {
    window.localStorage.setItem(READ_COMPLETIONS_KEY, JSON.stringify([...readCompletedSessions]));
  }, [readCompletedSessions]);

  useEffect(() => {
    if (runtime.state !== "ready" || runtime.storageHealth.state !== "ready") {
      return;
    }
    void Promise.all([
      window.eidosRuntime.listSessions(),
      window.eidosRuntime.getModelStatus(),
      window.eidosRuntime.listModels(),
      window.eidosRuntime.listPendingApprovals(),
    ]).then(([sessionPage, modelStatus, availableModels, pendingApprovals]) => {
      setSessions(sessionPage.items);
      const sessionIds = new Set(sessionPage.items.map((session) => session.id));
      setReadCompletedSessions((current) => new Set(
        [...current].filter((sessionId) => sessionIds.has(sessionId)),
      ));
      setModel(modelStatus);
      setModelList(availableModels);
      setSelectedModelId(availableModels.defaultModelId);
      setApprovals((current) => pendingApprovals.reduce(
        (merged, approval) => [
          ...merged.filter((item) => item.id !== approval.id),
          approval,
        ],
        current,
      ));
    }).catch((cause: unknown) => setError(messageFrom(cause)));
  }, [runtime]);

  useEffect(() => {
    if (!settingsOpen || runtime.state !== "ready" || runtime.storageHealth.state !== "ready") {
      return;
    }
    void refreshExtensions();
  }, [settingsOpen, runtime]);

  async function refreshExtensions(): Promise<void> {
    try {
      let extensionSnapshot = await window.eidosRuntime.readExtensions();
      const events = await window.eidosRuntime.readExtensionEvents(
        extensionSnapshot.throughEventId,
      );
      if (events.items.length > 0) {
        extensionSnapshot = await window.eidosRuntime.readExtensions();
      }
      setPlugins(extensionSnapshot.plugins);
      setSkills(extensionSnapshot.skills);
      setMcpServers(extensionSnapshot.servers);
    } catch (cause) {
      setError(messageFrom(cause));
    }
  }

  async function importPlugin(): Promise<void> {
    setBusy(true);
    setError(undefined);
    try {
      const imported = await window.eidosRuntime.importPlugin();
      if (imported) await refreshExtensions();
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function setPluginEnabled(pluginId: string, enabled: boolean): Promise<void> {
    setBusy(true);
    try {
      await window.eidosRuntime.setPluginEnabled(pluginId, enabled);
      await refreshExtensions();
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function removePlugin(pluginId: string): Promise<void> {
    if (!window.confirm("移除这个本地 Plugin？历史 Run 的来源记录会保留。")) return;
    setBusy(true);
    try {
      await window.eidosRuntime.removePlugin(pluginId);
      await refreshExtensions();
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function setMcpEnabled(server: McpServerRecord, enabled: boolean): Promise<void> {
    if (enabled && !window.confirm(
      `启用本地 MCP Server？\n\n命令：${[server.executable, ...server.argv].join(" ")}\nPlugin：${server.pluginId}@${server.pluginVersion}\n环境变量名：${server.envNames.join(", ") || "无"}\n权限：${server.permissionProfile}`,
    )) return;
    setBusy(true);
    try {
      await window.eidosRuntime.setMcpEnabled(server.pluginId, server.serverId, enabled);
      await refreshExtensions();
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function selectSession(session: Session): Promise<void> {
    setNavigationSessionId(session.id);
    if (selectedSessionId.current === session.id && snapshot?.session.id === session.id) {
      if (session.taskStatus === "completed") {
        setReadCompletedSessions((current) => new Set(current).add(session.id));
      }
      setSettingsOpen(false);
      setSessionMenuOpen(false);
      setRenaming(false);
      return;
    }
    const token = snapshotReads.select(session.id);
    selectedSessionId.current = session.id;
    if (session.taskStatus === "completed") {
      setReadCompletedSessions((current) => new Set(current).add(session.id));
    }
    setError(undefined);
    setSettingsOpen(false);
    setSessionMenuOpen(false);
    setRenaming(false);
    try {
      const loaded = await loadAuthoritativeSnapshot(session.id);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
        setSelectedModelId(accepted.runs[0]?.modelId ?? modelList?.defaultModelId ?? "deepseek-v4-flash");
      }
    } catch (cause) {
      if (snapshotReads.isCurrent(token)) {
        const fallbackSessionId = snapshot?.session.id;
        selectedSessionId.current = fallbackSessionId;
        snapshotReads.select(fallbackSessionId ?? "");
        setNavigationSessionId(fallbackSessionId);
        setError(messageFrom(cause));
      }
    }
  }

  async function createSession(workspaceRoot?: string): Promise<void> {
    const workspace = workspaceRoot ?? await window.eidosRuntime.selectWorkspace();
    if (!workspace) {
      return;
    }
    setBusy(true);
    setError(undefined);
    setSettingsOpen(false);
    try {
      const session = await window.eidosRuntime.createSession(workspace);
      const token = snapshotReads.select(session.id);
      selectedSessionId.current = session.id;
      setNavigationSessionId(session.id);
      setSessions((current) => [session, ...current]);
      const loaded = await loadAuthoritativeSnapshot(session.id);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
        setSelectedModelId(modelList?.defaultModelId ?? "deepseek-v4-flash");
      }
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function configureModel(): Promise<void> {
    setBusy(true);
    setError(undefined);
    try {
      setModel(await window.eidosRuntime.configureModel(apiKey));
      setModelList(await window.eidosRuntime.listModels());
      setApiKey("");
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function renameSession(): Promise<void> {
    if (!snapshot || !titleDraft.trim()) {
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const renamed = await window.eidosRuntime.renameSession(snapshot.session.id, titleDraft.trim());
      setSessions((current) => current.map((session) => session.id === renamed.id ? renamed : session));
      setSnapshot((current) => current && ({ ...current, session: renamed }));
      setRenaming(false);
      setSessionMenuOpen(false);
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function beginRename(session: Session): Promise<void> {
    if (snapshot?.session.id !== session.id) {
      await selectSession(session);
    }
    setTitleDraft(session.title ?? "新任务");
    setRenaming(true);
    setSessionMenuOpen(false);
  }

  async function deleteSession(session: Session): Promise<void> {
    if (!window.confirm(`删除任务“${session.title ?? "新任务"}”？项目文件不会被删除。`)) {
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const deleted = await window.eidosRuntime.deleteSession(session.id);
      const remaining = sessions.filter((session) => session.id !== deleted.deletedSessionId);
      setSessions(remaining);
      setReadCompletedSessions((current) => {
        const next = new Set(current);
        next.delete(deleted.deletedSessionId);
        return next;
      });
      setSessionMenuOpen(false);
      setRenaming(false);
      if (snapshot?.session.id === deleted.deletedSessionId) {
        setSnapshot(undefined);
        setNavigationSessionId(undefined);
        selectedSessionId.current = undefined;
        snapshotReads.select("");
        if (remaining[0]) {
          await selectSession(remaining[0]);
        }
      }
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function submitInput(): Promise<void> {
    if (!snapshot || !input.trim() || interactionBusy) {
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const run = continuingRun
        ? await window.eidosRuntime.continueRun(continuingRun.id, input.trim())
        : await window.eidosRuntime.startRun(snapshot.session.id, input.trim(), selectedModelId);
      setReadCompletedSessions((current) => {
        const next = new Set(current);
        next.delete(snapshot.session.id);
        return next;
      });
      setSnapshot((current) => current && ({
        ...current,
        runs: upsertRun(current.runs, run),
      }));
      if (!continuingRun && !snapshot.session.title) {
        const sessionPage = await window.eidosRuntime.listSessions();
        const titledSession = sessionPage.items.find((session) => session.id === snapshot.session.id);
        setSessions(sessionPage.items);
        if (titledSession) {
          setSnapshot((current) => current && ({ ...current, session: titledSession }));
        }
      }
      setInput("");
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function cancelRun(): Promise<void> {
    if (!activeRun) {
      return;
    }
    setBusy(true);
    try {
      const canceled = await window.eidosRuntime.cancelRun(activeRun.id);
      setSnapshot((current) => current && ({
        ...current,
        runs: upsertRun(current.runs, canceled),
      }));
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function respondApproval(
    request: ApprovalRequest,
    decision: "approve" | "reject",
  ): Promise<void> {
    setBusy(true);
    setError(undefined);
    try {
      const accepted = await window.eidosRuntime.respondApproval(request.id, decision);
      if (!accepted) {
        throw new Error("这个审批已经失效。");
      }
      setApprovals((current) => current.filter((item) => item.id !== request.id));
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function refreshCompletedSession(sessionId: string): Promise<void> {
    const token = snapshotReads.refresh(sessionId);
    if (!token) {
      return;
    }
    try {
      const loaded = await loadAuthoritativeSnapshot(sessionId);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
      }
    } catch (cause) {
      if (snapshotReads.isCurrent(token)) {
        setError(messageFrom(cause));
      }
    }
  }

  if (runtime.state !== "ready") {
    return <RuntimeGate status={runtime} />;
  }

  return (
    <main className="workbench">
      <SessionSidebar
        sessions={sessions}
        selectedId={navigationSessionId ?? snapshot?.session.id}
        disabled={interactionBusy || runtime.storageHealth.state !== "ready"}
        readCompletedSessions={readCompletedSessions}
        onCreate={() => void createSession()}
        onCreateInWorkspace={(workspaceRoot) => void createSession(workspaceRoot)}
        onSelect={(session) => void selectSession(session)}
        onRename={(session) => void beginRename(session)}
        onDelete={(session) => void deleteSession(session)}
        onOpenSettings={() => {
          setSettingsOpen(true);
          setSessionMenuOpen(false);
          setRenaming(false);
        }}
      />
      <section className="workspace" aria-label="Agent 工作区">
        {runtime.storageHealth.state !== "ready" && (
          <p className="error-banner" role="alert">
            状态存储处于只读健康模式（{runtime.storageHealth.code ?? "unknown"}），不会执行 Run 或写入状态。
          </p>
        )}

        {error && <p className="error-banner" role="alert">{error}</p>}

        {settingsOpen ? (
          <section className="settings-page" aria-labelledby="settings-title">
            <header className="workspace-header"><h1 id="settings-title">设置</h1></header>
            <div className="settings-content">
              <section className="settings-card">
                <h2>模型配置</h2>
                <p>支持的模型由 Runtime 返回；任务首次开始后将锁定本次使用的模型。</p>
                <ul className="model-list">
                  {modelList?.models.map((option) => (
                    <li key={option.id}>
                      <span><strong>{option.displayName}</strong><small>{option.id}</small></span>
                      <span>{option.configured ? "可用" : "待配置"}</span>
                    </li>
                  ))}
                </ul>
                <div className="key-row">
                  <label className="sr-only" htmlFor="api-key">DeepSeek API Key</label>
                  <input id="api-key" type="password" autoComplete="off" placeholder="sk-…" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
                  <button disabled={interactionBusy || runtime.storageHealth.state !== "ready" || apiKey.length < 16} onClick={() => void configureModel()}>
                    {model?.configured ? "更换 API Key" : "保存配置"}
                  </button>
                </div>
                <p className="settings-note">API Key 仅保存在本机 ~/.eidos/model.json（权限 0600），不会写入项目。</p>
              </section>
              <section className="settings-card">
                <h2>Runtime</h2>
                <dl className="runtime-details">
                  <div><dt>版本</dt><dd>{runtime.runtimeVersion}</dd></div>
                  <div><dt>Shell</dt><dd>{runtime.runShell ? "可用" : "Seatbelt 自检未通过"}</dd></div>
                  <div><dt>状态存储</dt><dd>{runtime.storageHealth.state}</dd></div>
                </dl>
              </section>
              <section className="settings-card">
                <div className="settings-card-heading">
                  <div><h2>Plugins</h2><p>只导入本地配置包，不执行安装脚本。</p></div>
                  <button disabled={interactionBusy} onClick={() => void importPlugin()}>导入本地 Plugin</button>
                </div>
                <ul className="extension-list">
                  {plugins.map((plugin) => (
                    <li key={plugin.id}>
                      <span><strong>{plugin.name}</strong><small>{plugin.id}@{plugin.version} · {plugin.contentHash.slice(0, 10)}</small></span>
                      <span className="extension-actions">
                        <button disabled={interactionBusy} onClick={() => void setPluginEnabled(plugin.id, !plugin.enabled)}>{plugin.enabled ? "停用" : "启用"}</button>
                        <button className="button-secondary" disabled={interactionBusy} onClick={() => void removePlugin(plugin.id)}>移除</button>
                      </span>
                    </li>
                  ))}
                  {!plugins.length && <li className="empty-extension">尚未导入 Plugin</li>}
                </ul>
              </section>
              <section className="settings-card">
                <h2>Skills</h2>
                <ul className="extension-list">
                  {skills.map((skill) => (
                    <li key={skill.qualifiedId}>
                      <span><strong>{skill.qualifiedId}</strong><small>{skill.description}</small></span>
                      <span>只读</span>
                    </li>
                  ))}
                  {!skills.length && <li className="empty-extension">没有已启用的 Skill</li>}
                </ul>
              </section>
              <section className="settings-card">
                <h2>MCP Servers</h2>
                <ul className="extension-list extension-list--stacked">
                  {mcpServers.map((server) => (
                    <li key={`${server.pluginId}:${server.serverId}`}>
                      <span>
                        <strong>{server.pluginId}:{server.serverId}</strong>
                        <small>{[server.executable, ...server.argv].join(" ")}</small>
                        <small>权限 {server.permissionProfile} · env {server.envNames.join(", ") || "无"}</small>
                        {server.errorCode && <small className="extension-error">{server.errorCode}</small>}
                      </span>
                      <button disabled={interactionBusy || !server.declaredEnabled} onClick={() => void setMcpEnabled(server, !server.consented)}>{server.consented ? "停用" : "审阅并启用"}</button>
                    </li>
                  ))}
                  {!mcpServers.length && <li className="empty-extension">没有 MCP Server 声明</li>}
                </ul>
              </section>
            </div>
          </section>
        ) : snapshot ? (
          <>
            <header className="workspace-header session-header">
              {renaming ? (
                <form className="rename-form" onSubmit={(event) => { event.preventDefault(); void renameSession(); }}>
                  <label className="sr-only" htmlFor="session-title">任务标题</label>
                  <input id="session-title" value={titleDraft} autoFocus onChange={(event) => setTitleDraft(event.target.value)} />
                  <button type="submit" disabled={interactionBusy || !titleDraft.trim()}>保存</button>
                  <button className="button-secondary" type="button" onClick={() => setRenaming(false)}>取消</button>
                </form>
              ) : (
                <h1 onContextMenu={(event) => { event.preventDefault(); setSessionMenuOpen(true); }}>{snapshot.session.title ?? "新任务"}</h1>
              )}
              <div className="session-menu">
                <button className="icon-button" aria-label="任务菜单" aria-expanded={sessionMenuOpen} onClick={() => setSessionMenuOpen((open) => !open)}>•••</button>
                {sessionMenuOpen && (
                  <div className="session-menu-popover" role="menu">
                    <button role="menuitem" onClick={() => void beginRename(snapshot.session)}>编辑标题</button>
                    <button role="menuitem" className="danger-action" disabled={Boolean(activeRun)} onClick={() => void deleteSession(snapshot.session)}>删除任务</button>
                  </div>
                )}
              </div>
            </header>
            <ExecutionFeed
              items={snapshot.items}
              runs={snapshot.runs}
              approvals={approvals.filter((approval) => approval.sessionId === snapshot.session.id)}
              disabled={interactionBusy}
              onApproval={(request, decision) => void respondApproval(request, decision)}
            />
            <form className="composer" onSubmit={(event) => {
              event.preventDefault();
              void submitInput();
            }}>
              <label className="sr-only" htmlFor="task-input">告诉 Eidos 要做什么</label>
              <textarea
                id="task-input"
                rows={2}
                placeholder={continuingRun ? "补充信息后继续这个 Run" : model?.configured ? "例如：阅读这个项目并说明如何启动" : "请先配置 DeepSeek API Key"}
                value={input}
                disabled={!model?.configured || interactionBusy || runtime.storageHealth.state !== "ready"}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                    event.preventDefault();
                    void submitInput();
                  }
                }}
              />
              <div className="composer-actions">
                <div className="composer-meta">
                  {!continuingRun && snapshot.runs.length === 0 ? (
                    <>
                      <label htmlFor="run-model">本次模型</label>
                      <select id="run-model" value={selectedModelId} disabled={interactionBusy} onChange={(event) => setSelectedModelId(event.target.value as ModelId)}>
                        {modelList?.models.map((option) => <option key={option.id} value={option.id} disabled={!option.selectable}>{option.displayName}</option>)}
                      </select>
                    </>
                  ) : <span>{continuingRun ? "等待你的补充" : activeRun ? statusText(activeRun.status) : selectedModelId}</span>}
                </div>
                {activeRun?.allowedActions?.includes("cancel") && !continuingRun ? (
                  <button className="button-secondary" type="button" disabled={interactionBusy} onClick={() => void cancelRun()}>
                    取消 Run
                  </button>
                ) : (
                  <button type="submit" disabled={interactionBusy || !model?.configured || !input.trim()}>
                    {continuingRun ? "继续" : "开始"}
                  </button>
                )}
              </div>
            </form>
          </>
        ) : (
          <div className="empty-state">
            <h2>我们该做点什么？</h2>
            <p>Eidos 可以阅读、修改所选工作空间的文件</p>
            <button disabled={interactionBusy || runtime.storageHealth.state !== "ready"} onClick={() => void createSession()}>选择目录</button>
          </div>
        )}
      </section>
    </main>
  );
}

async function loadAuthoritativeSnapshot(sessionId: string): Promise<SessionSnapshot> {
  let loaded = await window.eidosRuntime.readSession(sessionId);
  let after = loaded.throughEventId ?? 0;
  let changed = false;
  for (let page = 0; page < 10; page += 1) {
    const events = await window.eidosRuntime.listEvents(sessionId, after);
    changed ||= events.items.length > 0;
    after = events.throughEventId;
    if (!events.hasMore) {
      break;
    }
  }
  if (changed) {
    loaded = await window.eidosRuntime.readSession(sessionId);
  }
  return loaded;
}

function statusText(status: Run["status"]): string {
  return ({
    queued: "已排队", running: "正在执行", waiting_approval: "等待批准",
    waiting_user_input: "等待输入", finalizing: "正在收尾", stopped: "已停止",
    succeeded: "已完成", failed: "失败", canceled: "已取消", interrupted: "已中断",
  } as const)[status];
}

function RuntimeGate({ status }: { status: RuntimeStatus }) {
  return (
    <main className="runtime-gate" role={status.state === "error" ? "alert" : "status"}>
      <EidosMark className="runtime-logo" />
      <p className="eyebrow">Eidos · Local Runtime</p>
      <h1>{status.state === "error" ? "启动失败" : "正在启动"}</h1>
      <p>{status.state === "error" ? status.message : "正在完成 Runtime 协议握手…"}</p>
    </main>
  );
}

function upsertRun(runs: Run[], incoming: Run): Run[] {
  const existing = runs.findIndex((run) => run.id === incoming.id);
  if (existing < 0) {
    return [...runs, incoming];
  }
  return runs.map((run, index) => index === existing ? incoming : run);
}

function messageFrom(cause: unknown): string {
  return userFacingError(cause);
}
