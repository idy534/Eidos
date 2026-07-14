import { useEffect, useMemo, useState } from "react";

import type {
  Item,
  ApprovalRequest,
  ModelStatus,
  Run,
  RuntimeNotification,
  RuntimeStatus,
  Session,
  SessionSnapshot,
} from "./contracts";
import { ExecutionFeed } from "./components/ExecutionFeed";
import { SessionSidebar } from "./components/SessionSidebar";


export function App() {
  const [runtime, setRuntime] = useState<RuntimeStatus>({ state: "starting" });
  const [model, setModel] = useState<ModelStatus>();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [snapshot, setSnapshot] = useState<SessionSnapshot>();
  const [input, setInput] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);

  const activeRun = useMemo(
    () => snapshot?.runs.find((run) => ["running", "waiting_approval"].includes(run.status)),
    [snapshot],
  );

  useEffect(() => {
    const unsubscribeStatus = window.eidosRuntime.onStatus(setRuntime);
    const unsubscribeNotifications = window.eidosRuntime.onNotification((notification) => {
      if (notification.method === "run/completed") {
        setApprovals((current) => current.filter(
          (approval) => approval.runId !== notification.params.run.id,
        ));
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
    ]).then(([sessionPage, modelStatus]) => {
      setSessions(sessionPage.items);
      setModel(modelStatus);
    }).catch((cause: unknown) => setError(messageFrom(cause)));
  }, [runtime]);

  async function selectSession(session: Session): Promise<void> {
    setBusy(true);
    setError(undefined);
    try {
      setSnapshot(await window.eidosRuntime.readSession(session.id));
    } catch (cause) {
      setError(messageFrom(cause));
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
      setSessions((current) => [session, ...current]);
      setSnapshot(await window.eidosRuntime.readSession(session.id));
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
    } catch (cause) {
      setError(messageFrom(cause));
    } finally {
      setBusy(false);
    }
  }

  async function startRun(): Promise<void> {
    if (!snapshot || !input.trim()) {
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

  if (runtime.state !== "ready") {
    return <RuntimeGate status={runtime} />;
  }

  return (
    <main className="workbench">
      <SessionSidebar
        sessions={sessions}
        selectedId={snapshot?.session.id}
        disabled={busy}
        onCreate={() => void createSession()}
        onSelect={(session) => void selectSession(session)}
      />
      <section className="workspace" aria-label="Agent 工作区">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">Developer Preview · L1</p>
            <h1>{snapshot ? "Eidos Workspace" : "Eidos"}</h1>
            <p className="workspace-path">
              {snapshot?.session.workspaceRoot ?? "选择一个本地目录开始。"}
            </p>
          </div>
          <span className="runtime-pill">Runtime {runtime.runtimeVersion}</span>
        </header>

        {!model?.configured && (
          <section className="setup-panel" aria-labelledby="model-title">
            <div>
              <h2 id="model-title">连接 DeepSeek</h2>
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
              <button disabled={busy || apiKey.length < 16} onClick={() => void configureModel()}>
                保存配置
              </button>
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
              disabled={busy}
              onApproval={(request, decision) => void respondApproval(request, decision)}
            />
            <form className="composer" onSubmit={(event) => {
              event.preventDefault();
              void startRun();
            }}>
              <label className="sr-only" htmlFor="task-input">告诉 Eidos 要做什么</label>
              <textarea
                id="task-input"
                rows={3}
                placeholder={model?.configured ? "例如：阅读这个项目并说明如何启动" : "请先配置 DeepSeek API Key"}
                value={input}
                disabled={!model?.configured || Boolean(activeRun)}
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
                  <button className="button-secondary" type="button" disabled={busy} onClick={() => void cancelRun()}>
                    取消 Run
                  </button>
                ) : (
                  <button type="submit" disabled={busy || !model?.configured || !input.trim()}>
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
            <p>Eidos 目前只会读取你选择的目录；写入和 Shell 将在下一阶段加入审批。</p>
            <button disabled={busy} onClick={() => void createSession()}>选择目录</button>
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

function applyNotification(
  snapshot: SessionSnapshot | undefined,
  notification: RuntimeNotification,
): SessionSnapshot | undefined {
  if (!snapshot || notification.params.sessionId !== snapshot.session.id) {
    return snapshot;
  }
  if (notification.method === "run/started" || notification.method === "run/completed") {
    return { ...snapshot, runs: upsertRun(snapshot.runs, notification.params.run) };
  }
  if (notification.method === "item/delta") {
    return {
      ...snapshot,
      items: snapshot.items.map((item) => item.id === notification.params.itemId
        ? { ...item, content: `${item.content ?? ""}${notification.params.delta}` }
        : item),
    };
  }
  const incoming = notification.params.item;
  const existing = snapshot.items.find((item) => item.id === incoming.id);
  let merged: Item = existing?.content !== undefined && incoming.content === undefined
    ? { ...incoming, content: existing.content }
    : incoming;
  if (existing?.toolCall && incoming.toolCall) {
    merged = { ...merged, toolCall: { ...existing.toolCall, ...incoming.toolCall } };
  }
  return { ...snapshot, items: upsertItem(snapshot.items, merged) };
}

function upsertRun(runs: Run[], incoming: Run): Run[] {
  const existing = runs.findIndex((run) => run.id === incoming.id);
  if (existing < 0) {
    return [...runs, incoming];
  }
  return runs.map((run, index) => index === existing ? incoming : run);
}

function upsertItem(items: Item[], incoming: Item): Item[] {
  const existing = items.findIndex((item) => item.id === incoming.id);
  if (existing < 0) {
    return [...items, incoming].sort((left, right) => left.ordinal - right.ordinal);
  }
  return items.map((item, index) => index === existing ? incoming : item);
}

function messageFrom(cause: unknown): string {
  return cause instanceof Error ? cause.message : "操作失败，请查看 Runtime 日志。";
}
