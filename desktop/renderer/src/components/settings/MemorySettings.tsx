import { useEffect, useRef, useState } from "react";
import type { MemoryEntry, MemoryManageRequest, MemorySettings as Settings, MemoryState } from "../../../../shared/memory.generated.js";
import { Button } from "../Button.js";
import { SettingSection } from "./SettingSection.js";
import { SettingRow } from "./SettingRow.js";
import { Toggle } from "./Toggle.js";
import { ConfirmDialog } from "./ConfirmDialog.js";

const statusLabels: Record<string, string> = {active: "已生效", candidate: "待确认", archived: "已归档", superseded: "历史版本", quarantined: "来源失效", forgotten: "已遗忘"};
const jobLabels: Record<string, string> = {queued: "等待整理", running: "正在整理", succeeded: "已完成", retry_wait: "等待重试", paused_budget: "预算暂停", blocked_model: "模型配置不可用", superseded: "来源已更新", canceled: "已取消", failed: "整理失败"};

export function MemorySettings({sessionId, onOpenSource}: {sessionId?: string | undefined; onOpenSource?: ((id: string) => void) | undefined}) {
  const [state, setState] = useState<MemoryState>();
  const [scope, setScope] = useState<"current" | "global">(sessionId ? "current" : "global");
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [history, setHistory] = useState(false);
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState<MemoryEntry>();
  const [editContent, setEditContent] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [confirmation, setConfirmation] = useState<{title: string; description: string; action: () => Promise<unknown>}>();
  const generation = useRef(0);

  useEffect(() => {
    const token = ++generation.current;
    let canceled = false;
    let pending = false;
    async function load() {
      if (pending) return;
      pending = true;
      try {
        const value = await window.eidosRuntime.memory("memory/list", {sessionId: sessionId ?? null, scope, query, cursor, includeHistory: history});
        if (!canceled && token === generation.current) setState(value);
      } catch {
        if (!canceled) setError("无法读取记忆，请检查 Runtime 状态。");
      } finally { pending = false; }
    }
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => { canceled = true; window.clearInterval(timer); };
  }, [sessionId, scope, query, cursor, history, refresh]);

  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await action(); setConfirmation(undefined); setEditing(undefined); setRefresh((value) => value + 1); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "记忆操作失败，请刷新后重试。"); }
    finally { setBusy(false); }
  }

  function manage(entry: MemoryEntry, action: MemoryManageRequest["action"], revised?: string) {
    return window.eidosRuntime.memory("memory/manage", {sessionId: sessionId ?? null, entryId: entry.id, expectedRevision: entry.revision,
      action, operationId: crypto.randomUUID(), ...(revised === undefined ? {} : {content: revised})});
  }
  const selected = state?.scopes[0];
  function update(settings: Settings) {
    return perform(() => window.eidosRuntime.memory("memory/settingsUpdate", {sessionId: sessionId ?? null, scope, settings}));
  }

  return <SettingSection title="记忆" description="记忆保存偏好、决策和有来源的经验。自动学习默认关闭；开启后会将后续对话发送给所选模型整理，并产生模型费用。">
    {error && <p className="error-banner" role="alert">{error}</p>}
    <label>作用域 <select aria-label="记忆作用域" value={scope} disabled={busy} onChange={(event) => {setScope(event.target.value as "current" | "global"); setState(undefined); setCursor(0);}}>
      {sessionId && <option value="current">当前项目（无项目时为全局）</option>}<option value="global">全局</option>
    </select></label>
    {!selected ? <p role="status">正在加载记忆…</p> : <>
      <SettingRow title="使用记忆" description="关闭后不再向模型提供本作用域记忆。" action={<Toggle label="使用记忆" checked={selected.settings.useEnabled ?? true} disabled={busy} onChange={(checked) => void update({...selected.settings, useEnabled: checked})}/>}/>
      <SettingRow title="自动生成记忆" description="仅整理开启后的原始对话，后台失败不影响前台任务。" action={<Toggle label="自动生成记忆" checked={selected.settings.generateEnabled ?? false} disabled={busy} onChange={(checked) => {
        if (checked) setConfirmation({title: "开启自动学习", description: "后续对话将按当前模型配置发送给模型，最多执行提炼和整理两阶段请求。历史对话需单独选择回填。", action: () => window.eidosRuntime.memory("memory/settingsUpdate", {sessionId: sessionId ?? null, scope, settings: {...selected.settings, generateEnabled: true}})});
        else void update({...selected.settings, generateEnabled: false});
      }}/>}/>
      <SettingRow title="每日后台预算" description="达到上限时暂停至下一天；无法取得真实用量时使用保守估计。">
        <label>请求次数 <input aria-label="每日记忆请求次数" type="number" min={0} max={1000} disabled={busy} value={selected.settings.dailyCallLimit ?? 20} onChange={(event) => {const value = Number(event.target.value); if (Number.isInteger(value) && value >= 0 && value <= 1000) void update({...selected.settings, dailyCallLimit: value});}}/></label>
        <label>Token 上限 <input aria-label="每日记忆 Token 上限" type="number" min={0} max={10000000} disabled={busy} value={selected.settings.dailyTokenLimit ?? 100000} onChange={(event) => {const value = Number(event.target.value); if (Number.isInteger(value) && value >= 0 && value <= 10000000) void update({...selected.settings, dailyTokenLimit: value});}}/></label>
      </SettingRow>
      {sessionId && <SettingRow title="临时对话" description="此对话不使用或生成记忆；已有依赖此对话的自动记忆会失效。" action={<Toggle label="临时对话" checked={state?.temporary ?? false} disabled={busy} onChange={(temporary) => void perform(() => window.eidosRuntime.memory("memory/temporary", {sessionId, temporary}))}/>}/>}
      {sessionId && <Button variant="secondary" disabled={busy || !selected.settings.generateEnabled || state?.temporary} onClick={() => setConfirmation({title: "整理当前对话历史", description: "仅回填当前选中的对话。原始内容会发送给模型并计入后台预算；已遗忘的来源不会重新学习。", action: () => window.eidosRuntime.memory("memory/backfill", {sessionId, operationId: crypto.randomUUID()})})}>回填当前对话</Button>}
      <form onSubmit={(event) => {event.preventDefault(); void perform(async () => {await window.eidosRuntime.memory("memory/remember", {sessionId: sessionId ?? null, operationId: crypto.randomUUID(), scope, content}); setContent("");});}}>
        <label>新增记忆<textarea aria-label="新增记忆内容" maxLength={8192} value={content} disabled={busy} onChange={(event) => setContent(event.target.value)}/></label>
        <Button type="submit" disabled={busy || !content.trim()}>记住</Button>
      </form>
      <label>搜索 <input aria-label="搜索记忆" maxLength={512} value={query} onChange={(event) => {setQuery(event.target.value); setCursor(0);}}/></label>
      <label><input type="checkbox" checked={history} onChange={(event) => {setHistory(event.target.checked); setCursor(0);}}/>显示归档和历史状态</label>
      <p>{state?.trigramAvailable ? "支持中文片段和英文词检索" : "使用词检索和有界短词检索"}</p>
      <div className="memory-entry-list">{state?.entries.map((entry) => <article className="memory-entry" key={entry.id}>
        <h3>{entry.title || entry.kind} <small>{statusLabels[entry.status]} · v{entry.revision}{entry.pinned ? " · 已固定" : ""}</small></h3>
        <p className="memory-content">{entry.content}</p>
        {entry.status === "superseded" && <Button size="small" variant="ghost" onClick={() => {setQuery(entry.id); setHistory(false); setCursor(0);}}>查看当前版本</Button>}
        <p>{entry.userOwned ? "用户独立保存" : `来源：${entry.evidenceClass}`} · {new Date(entry.updatedAt).toLocaleString()}</p>
        {entry.validFrom != null && <p>有效期：{new Date(entry.validFrom).toLocaleDateString()} — {entry.validTo == null ? "未设结束日期" : new Date(entry.validTo).toLocaleDateString()}</p>}
        {entry.evidence.map((source) => <Button key={source.itemId} size="small" variant="ghost" disabled={!onOpenSource} onClick={() => onOpenSource?.(source.sessionId)}>查看来源对话</Button>)}
        <Button size="small" variant="secondary" disabled={busy || entry.status === "superseded"} onClick={() => {setEditing(entry); setEditContent(entry.content);}}>纠正</Button>
        <Button size="small" variant="ghost" disabled={busy || entry.status === "superseded"} onClick={() => void perform(() => manage(entry, entry.pinned ? "unpin" : "pin"))}>{entry.pinned ? "取消固定" : "固定"}</Button>
        {entry.status === "candidate" && <Button size="small" disabled={busy} onClick={() => void perform(() => manage(entry, "accept"))}>确认生效</Button>}
        {entry.status !== "archived" && <Button size="small" variant="ghost" disabled={busy || entry.status === "superseded"} onClick={() => void perform(() => manage(entry, "archive"))}>归档</Button>}
        <Button size="small" variant="danger" disabled={busy || entry.status === "superseded"} onClick={() => setConfirmation({title: "遗忘这条记忆", description: "删除所有正文版本并撤销相关上下文。同一来源不会再次自动学习；原始聊天由你单独管理。旧备份或已发送给模型的内容不能由此撤回。", action: () => manage(entry, "forget")})}>遗忘</Button>
      </article>)}</div>
      {state?.entries.length === 0 && <p>没有符合条件的记忆。</p>}
      <Button variant="ghost" disabled={!cursor || busy} onClick={() => setCursor(0)}>返回第一页</Button>
      {state?.truncated && state.nextCursor != null && <Button variant="secondary" disabled={busy} onClick={() => setCursor(state.nextCursor ?? 0)}>下一页</Button>}
      <Button variant="secondary" disabled={busy} onClick={() => void perform(async () => {
        const exported = await window.eidosRuntime.memory("memory/export", {sessionId: sessionId ?? null, scope, query, cursor, includeHistory: history});
        const url = URL.createObjectURL(new Blob([exported.markdown], {type: "text/markdown;charset=utf-8"}));
        const anchor = document.createElement("a"); anchor.href = url; anchor.download = "eidos-memories.md"; anchor.click(); URL.revokeObjectURL(url);
      })}>导出当前页（明文）</Button>
      <Button variant="secondary" disabled={busy} onClick={() => setConfirmation({title: "备份记忆和对话", description: "备份包含 SQLite 状态、所有有效记忆版本及对话上下文，保存为未加密 ZIP。请妥善保管；旧备份可能恢复已经遗忘的内容。恢复需要退出应用并使用新的数据目录。", action: () => window.eidosRuntime.backupMemory()})}>完整备份（明文 ZIP）</Button>
      <Button variant="ghost" disabled={busy} onClick={() => void perform(() => window.eidosRuntime.memory("memory/rebuild", {sessionId: sessionId ?? null}))}>重建检索索引</Button>
      <h3>后台整理</h3>
      <ul>{state?.jobs.map((job) => <li key={job.id}>{jobLabels[job.state]} · {job.modelId} · {job.tokens} tokens{job.estimatedUsage ? "（估计）" : ""}{job.errorCode ? ` · ${job.errorCode}` : ""}
        {["failed", "blocked_model", "retry_wait", "paused_budget"].includes(job.state) && <Button size="small" variant="ghost" disabled={busy} onClick={() => void perform(() => window.eidosRuntime.memory("memory/jobsRetry", {sessionId: sessionId ?? null, jobId: job.id}))}>确认当前模型配置并重试</Button>}
      </li>)}</ul>
    </>}
    <ConfirmDialog open={confirmation !== undefined} title={confirmation?.title ?? ""} description={confirmation?.description ?? ""} busy={busy} error={error || undefined} isDestructive={confirmation?.title.startsWith("遗忘") ?? false} onCancel={() => setConfirmation(undefined)} onConfirm={() => {if (confirmation) void perform(confirmation.action);}}/>
    <ConfirmDialog open={editing !== undefined} title="纠正记忆" description={<label>正文<textarea aria-label="纠正记忆正文" value={editContent} maxLength={8192} onChange={(event) => setEditContent(event.target.value)}/></label>} busy={busy} error={error || undefined} onCancel={() => setEditing(undefined)} onConfirm={() => {if (editing && editContent.trim()) void perform(() => manage(editing, "correct", editContent));}}/>
  </SettingSection>;
}
