import { useEffect, useMemo, useRef, useState } from "react";

import type {
  ApprovalRequest,
  ModelStatus,
  Run,
  RuntimeStatus,
  Session,
  SessionSnapshot,
} from "./contracts";
import { ExecutionFeed } from "./components/ExecutionFeed";
import { SessionSidebar } from "./components/SessionSidebar";
import { applyNotification, SnapshotReadCoordinator } from "./session-state";


export function App() {
  const [runtime, setRuntime] = useState<RuntimeStatus>({ state: "starting" });
  const [model, setModel] = useState<ModelStatus>();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [snapshot, setSnapshot] = useState<SessionSnapshot>();
  const [input, setInput] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [editingModel, setEditingModel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refreshingSnapshot, setRefreshingSnapshot] = useState(false);
  const [error, setError] = useState<string>();
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const snapshotReads = useRef(new SnapshotReadCoordinator()).current;

  const activeRun = useMemo(
    () => snapshot?.runs.find((run) => ["running", "waiting_approval"].includes(run.status)),
    [snapshot],
  );
  const interactionBusy = busy || refreshingSnapshot;

  useEffect(() => {
    const unsubscribeStatus = window.eidosRuntime.onStatus(setRuntime);
    const unsubscribeNotifications = window.eidosRuntime.onNotification((notification) => {
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
    if (runtime.state !== "ready") {
      return;
    }
    void Promise.all([
      window.eidosRuntime.listSessions(),
      window.eidosRuntime.getModelStatus(),
      window.eidosRuntime.listPendingApprovals(),
    ]).then(([sessionPage, modelStatus, pendingApprovals]) => {
      setSessions(sessionPage.items);
      setModel(modelStatus);
      setApprovals((current) => pendingApprovals.reduce(
        (merged, approval) => [
          ...merged.filter((item) => item.id !== approval.id),
          approval,
        ],
        current,
      ));
    }).catch((cause: unknown) => setError(messageFrom(cause)));
  }, [runtime]);

  async function selectSession(session: Session): Promise<void> {
    const token = snapshotReads.select(session.id);
    setRefreshingSnapshot(false);
    setBusy(true);
    setError(undefined);
    try {
      const loaded = await window.eidosRuntime.readSession(session.id);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
      }
    } catch (cause) {
      if (snapshotReads.isCurrent(token)) {
        setError(messageFrom(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  async function createSession(): Promise<void> {
    const workspace = await window.eidosRuntime.selectWorkspace();
    if (!workspace) {
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const session = await window.eidosRuntime.createSession(workspace);
      const token = snapshotReads.select(session.id);
      setRefreshingSnapshot(false);
      setSessions((current) => [session, ...current]);
      const loaded = await window.eidosRuntime.readSession(session.id);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
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
      setApiKey("");
      setEditingModel(false);
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function startRun(): Promise<void> {
    if (!snapshot || !input.trim() || interactionBusy) {
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const run = await window.eidosRuntime.startRun(snapshot.session.id, input.trim());
      setSnapshot((current) => current && ({
        ...current,
        runs: upsertRun(current.runs, run),
      }));
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
    setRefreshingSnapshot(true);
    try {
      const loaded = await window.eidosRuntime.readSession(sessionId);
      const accepted = snapshotReads.accept(token, loaded);
      if (accepted) {
        setSnapshot(accepted);
      }
    } catch (cause) {
      if (snapshotReads.isCurrent(token)) {
        setError(messageFrom(cause));
      }
    } finally {
      if (snapshotReads.isCurrent(token)) {
        setRefreshingSnapshot(false);
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
        selectedId={snapshot?.session.id}
        disabled={interactionBusy || Boolean(activeRun)}
        onCreate={() => void createSession()}
        onSelect={(session) => void selectSession(session)}
      />
      <section className="workspace" aria-label="Agent 工作区">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Developer Preview · MVP Lite</p>
            <h1>{snapshot ? "Eidos Workspace" : "Eidos"}</h1>
            <p className="workspace-path">
              {snapshot?.session.workspaceRoot ?? "选择一个本地目录开始。"}
            </p>
            <p className="preview-limit">
              文件写入与 Shell 每次都需批准；当前不提供内容级 Secret 检测，也不支持后台守护进程，请勿选择含敏感数据的 Workspace。
            </p>
            {!runtime.runShell && (
              <p className="shell-unavailable" role="status">
                Shell 当前不可用：Seatbelt 自检未通过。文件读取与经审批的文件修改仍可使用。
              </p>
            )}
          </div>
          <span className="runtime-pill">Runtime {runtime.runtimeVersion}</span>
        </header>

        {model?.configured && !editingModel && (
          <section className="model-status" aria-label="模型配置">
            <span>DeepSeek · deepseek-v4-flash 已配置</span>
            <button
              className="button-secondary"
              disabled={interactionBusy || Boolean(activeRun)}
              onClick={() => setEditingModel(true)}
            >
              更换 API Key
            </button>
          </section>
        )}

        {(!model?.configured || editingModel) && (
          <section className="setup-panel" aria-labelledby="model-title">
            <div>
              <h2 id="model-title">{model?.configured ? "更换 DeepSeek API Key" : "连接 DeepSeek"}</h2>
              <p>API Key 仅保存在本机 ~/.eidos/model.json（权限 0600），不会写入项目。</p>
            </div>
            <div className="key-row">
              <label className="sr-only" htmlFor="api-key">DeepSeek API Key</label>
              <input
                id="api-key"
                type="password"
                autoComplete="off"
                placeholder="sk-…"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
              />
              <button disabled={interactionBusy || apiKey.length < 16} onClick={() => void configureModel()}>
                保存配置
              </button>
              {model?.configured && (
                <button
                  className="button-secondary"
                  disabled={interactionBusy}
                  onClick={() => {
                    setApiKey("");
                    setEditingModel(false);
                  }}
                >
                  取消
                </button>
              )}
            </div>
          </section>
        )}

        {error && <p className="error-banner" role="alert">{error}</p>}

        {snapshot ? (
          <>
            <ExecutionFeed
              items={snapshot.items}
              runs={snapshot.runs}
              approvals={approvals.filter((approval) => approval.sessionId === snapshot.session.id)}
              disabled={interactionBusy}
              onApproval={(request, decision) => void respondApproval(request, decision)}
            />
            <form className="composer" onSubmit={(event) => {
              event.preventDefault();
              void startRun();
            }}>
              <label className="sr-only" htmlFor="task-input">告诉 Eidos 要做什么</label>
              <textarea
                id="task-input"
                rows={2}
                placeholder={model?.configured ? "例如：阅读这个项目并说明如何启动" : "请先配置 DeepSeek API Key"}
                value={input}
                disabled={!model?.configured || interactionBusy || Boolean(activeRun)}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                    event.preventDefault();
                    void startRun();
                  }
                }}
              />
              <div className="composer-actions">
                <span>{activeRun ? "正在执行…" : "⌘↵ 发送"}</span>
                {activeRun ? (
                  <button className="button-secondary" type="button" disabled={interactionBusy} onClick={() => void cancelRun()}>
                    取消 Run
                  </button>
                ) : (
                  <button type="submit" disabled={interactionBusy || !model?.configured || !input.trim()}>
                    开始
                  </button>
                )}
              </div>
            </form>
          </>
        ) : (
          <div className="empty-state">
            <p className="empty-kicker">Session → Run → Item</p>
            <h2>从一个 Workspace 开始</h2>
            <p>Eidos 可以阅读、修改和测试所选目录；每次文件写入与 Shell 命令都会先展示候选操作并等待批准。</p>
            <button disabled={interactionBusy} onClick={() => void createSession()}>选择目录</button>
          </div>
        )}
      </section>
    </main>
  );
}

function RuntimeGate({ status }: { status: RuntimeStatus }) {
  return (
    <main className="runtime-gate" role={status.state === "error" ? "alert" : "status"}>
      <p className="eyebrow">Eidos · Local Runtime</p>
      <h1>{status.state === "error" ? "启动失败" : "正在启动"}</h1>
      <p>{status.state === "error" ? status.message : "正在完成 Python Runtime 协议握手…"}</p>
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
  return cause instanceof Error ? cause.message : "操作失败，请查看 Runtime 日志。";
}
