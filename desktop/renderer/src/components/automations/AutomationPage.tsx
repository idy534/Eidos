import { useCallback, useEffect, useRef, useState } from "react";
import type { Automation, AutomationOccurrence, AutomationRequest, AutomationSummary } from "../../../../shared/automation.js";
import type { ModelOption, Project, Session } from "../../contracts.js";
import { Button } from "../Button.js";
import { AutomationForm } from "./AutomationForm.js";
import { userFacingError } from "../../session-state.js";

interface Props {
  projects: Project[];
  sessions: Session[];
  models: ModelOption[];
  currentSessionId?: string | undefined;
  currentProjectId?: string | undefined;
  onClose: () => void;
  onOpenSession: (id: string) => void;
}

export function AutomationPage({ projects, sessions, models, currentSessionId, currentProjectId, onClose, onOpenSession }: Props) {
  const [items, setItems] = useState<AutomationSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Automation | null>(null);
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [history, setHistory] = useState<AutomationOccurrence[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const selection = useRef(0);
  const listVersion = useRef(0);
  const mounted = useRef(true);
  const refresh = useCallback(async (after?: string) => {
    const version = ++listVersion.current;
    try {
      const result = await window.eidosRuntime.automation({ command: { mode: "view", scope: "all", limit: 20, ...(after ? { cursor: after } : {}) } });
      if (!mounted.current || version !== listVersion.current) return;
      setItems((current) => after ? [...current, ...(result.items ?? [])] : result.items ?? []);
      setCursor(result.nextCursor ?? null);
    } catch (failure) { if (mounted.current && version === listVersion.current) setError(userFacingError(failure)); }
    finally { if (mounted.current && version === listVersion.current) setLoading(false); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const unsubscribe = window.eidosRuntime.onNotification((notification) => {
      if (notification.method === "automation/changed" || notification.method === "run/completed") void refresh();
    });
    return () => { mounted.current = false; selection.current++; unsubscribe(); };
  }, [refresh]);
  useEffect(() => {
    if (!selected?.id) return;
    const id = selected.id;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = window.eidosRuntime.onNotification((notification) => {
      if (!((notification.method === "automation/changed" && notification.params.id === id)
        || notification.method === "run/completed" || notification.method === "run/updated")) return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        void Promise.all([window.eidosRuntime.automationHistory({ id }), editing ? Promise.resolve(null)
          : window.eidosRuntime.automation({ command: { mode: "view", id } })]).then(([records, detail]) => {
          if (disposed) return;
          setHistory(records.occurrences ?? []); setHistoryCursor(records.nextCursor ?? null);
          if (detail?.automation) setSelected(detail.automation);
        }).catch((failure) => { if (!disposed) setError(userFacingError(failure)); });
      }, 50);
    });
    return () => { disposed = true; clearTimeout(timer); unsubscribe(); };
  }, [selected?.id, editing]);
  const read = async (id: string) => {
    const version = ++selection.current;
    setError(""); setCreating(false); setEditing(false); setBusy(true);
    try {
      const [detail, records] = await Promise.all([
        window.eidosRuntime.automation({ command: { mode: "view", id } }), window.eidosRuntime.automationHistory({ id }),
      ]);
      if (version !== selection.current) return;
      setSelected(detail.automation ?? null); setHistory(records.occurrences ?? []); setHistoryCursor(records.nextCursor ?? null);
    } catch (failure) { if (version === selection.current) setError(userFacingError(failure)); }
    finally { if (version === selection.current) setBusy(false); }
  };
  const save = async (command: AutomationRequest) => {
    setBusy(true); setError("");
    try {
      const result = await window.eidosRuntime.automation({ operationId: crypto.randomUUID(), command,
        ...(currentSessionId ? { contextSessionId: currentSessionId } : {}) });
      setSelected(result.automation ?? null); setCreating(false); setEditing(false);
      await refresh();
    } catch (failure) { setError(userFacingError(failure)); throw failure; }
    finally { setBusy(false); }
  };
  const mutate = async (mode: "delete" | "update") => {
    if (!selected) return;
    try { await save({ mode, id: selected.id, expectedRevision: selected.revision,
      ...(mode === "update" ? { changes: { status: selected.status === "ACTIVE" ? "PAUSED" : "ACTIVE" } } : {}) }); }
    catch { /* The shared action displays the error and keeps the selected version. */ }
  };
  const runNow = async () => {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      await window.eidosRuntime.automationRunNow({ id: selected.id, operationId: crypto.randomUUID() });
      await read(selected.id);
    } catch (failure) { setError(userFacingError(failure)); }
    finally { setBusy(false); }
  };
  const moreHistory = async () => {
    if (!selected || !historyCursor) return;
    const id = selected.id; const version = selection.current;
    try {
      const result = await window.eidosRuntime.automationHistory({ id, cursor: historyCursor });
      if (version !== selection.current) return;
      setHistory((current) => [...current, ...(result.occurrences ?? [])]); setHistoryCursor(result.nextCursor ?? null);
    } catch (failure) { setError(userFacingError(failure)); }
  };
  return <div className="automation-page">
    <header className="automation-header"><div><h1>定时任务</h1><p>任务在本机执行。Eidos 退出或电脑休眠期间不会执行。</p></div><div className="automation-actions"><Button disabled={busy} onClick={() => { selection.current++; setCreating(true); setSelected(null); setHistory([]); setBusy(false); }}>新建任务</Button><Button variant="ghost" onClick={onClose}>返回对话</Button></div></header>
    {error && <div className="approval-error" role="alert">{error} <Button size="small" onClick={() => void refresh()}>刷新列表</Button></div>}
    <div className="automation-columns"><aside className="automation-list" aria-label="定时任务列表">
      {loading ? <p role="status">正在读取任务…</p> : !items.length && <p>还没有定时任务。你可以在这里创建，也可以在对话中提出要求。</p>}
      {items.map((task) => <button disabled={busy} className={`automation-list-item${selected?.id === task.id ? " selected" : ""}`} key={task.id} onClick={() => void read(task.id)}><strong>{task.name}</strong><span>{task.status === "ACTIVE" ? "已启用" : task.pauseReason === "one_shot_fired" ? "已结束" : "已暂停"} · {task.target.kind === "existing_session" ? "已有会话" : "每次新建会话"}</span><span>{task.nextRunAt ? new Date(task.nextRunAt).toLocaleString() : task.pauseReason ?? "暂无下一次执行"}</span></button>)}
      {cursor && <Button size="small" onClick={() => void refresh(cursor)}>加载更多任务</Button>}
    </aside><section className="automation-detail" aria-label="任务详情">
      {creating || (editing && selected) ? <AutomationForm key={creating ? "new" : `${selected?.id}:${selected?.revision}`} task={creating ? undefined : selected ?? undefined} projects={projects} sessions={sessions} models={models} currentSessionId={currentSessionId} currentProjectId={currentProjectId} busy={busy} onSave={save} /> : selected ? <>
        <h2>{selected.name}</h2><p className="automation-prompt">{selected.prompt}</p>
        <dl className="automation-facts"><dt>时间规则</dt><dd>{selected.schedule.expression ?? selected.schedule.runAt} · {selected.schedule.timezone}</dd><dt>执行位置</dt><dd>{selected.target.kind === "existing_session" ? "已有会话" : "每次新建会话"} · {projects.find((project) => project.id === selected.target.projectId)?.name ?? (selected.target.projectId ? "项目不可用" : "无项目")}</dd><dt>模型</dt><dd>{models.find((model) => model.id === selected.model.modelId)?.name ?? selected.model.modelId}</dd><dt>审批模式</dt><dd>{selected.approvalMode === "auto_review" ? "替我审批" : "请求审批"}</dd></dl>
        {selected.pauseReason && <p role="status">{selected.pauseReason === "one_shot_fired" ? "这个一次性任务已经结束。" : selected.pauseReason}</p>}
        {selected.deletedAt ? <p>任务已删除，执行历史仍然保留。</p> : <div className="automation-actions"><Button disabled={busy} onClick={() => setEditing(true)}>编辑</Button><Button disabled={busy} onClick={() => void mutate("update")}>{selected.status === "ACTIVE" ? "暂停" : "恢复"}</Button><Button disabled={busy || selected.status !== "ACTIVE"} onClick={() => void runNow()}>立即执行</Button><Button disabled={busy} onClick={() => void mutate("delete")}>删除计划</Button></div>}
        <h3>执行历史</h3>{!history.length && <p>还没有执行记录。</p>}
        <ul className="automation-history">{history.map((record) => <li key={record.id}><span>{record.scheduledFor.startsWith("manual:") ? "手动执行" : new Date(record.scheduledFor).toLocaleString()} · {(record.reconciliationRequired ? "操作结果待核验" : record.runStatus ? ({ queued: "等待执行", running: "执行中", waiting_input: "等待回答", waiting_approval: "等待审批", waiting_agents: "等待子任务", finalizing: "正在结束", succeeded: "已完成", stopped: "已结束", failed: "执行失败", canceled: "已取消", interrupted: "执行已中断" } as Record<string, string>)[record.runStatus] : null) ?? ({ preparing: "准备中", submitted: "已提交执行", skipped: "已跳过", failed: "准备失败", interrupted: "准备已中断" })[record.status]}</span>{record.reason && <p>{record.reason}</p>}{record.sessionId && <Button size="small" onClick={() => onOpenSession(record.sessionId!)}>查看执行结果</Button>}</li>)}</ul>
        {historyCursor && <Button size="small" onClick={() => void moreHistory()}>加载更多记录</Button>}
      </> : <p className="automation-empty">选择一个任务查看配置和执行结果，或创建新任务。</p>}
    </section></div>
  </div>;
}
