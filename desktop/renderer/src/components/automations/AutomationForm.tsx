import { useState } from "react";
import type { Automation, AutomationRequest } from "../../../../shared/automation.js";
import type { ModelOption, ModelReasoningSelection, Project, Session } from "../../contracts.js";
import { Button } from "../Button.js";
import { userFacingError } from "../../session-state.js";

interface Props {
  task?: Automation | undefined;
  projects: Project[];
  sessions: Session[];
  models: ModelOption[];
  currentSessionId?: string | undefined;
  currentProjectId?: string | undefined;
  busy: boolean;
  onSave: (request: AutomationRequest) => Promise<void>;
}

export function AutomationForm({ task, projects, sessions, models, currentSessionId, currentProjectId, busy, onSave }: Props) {
  const current = sessions.find((session) => session.id === currentSessionId);
  const [name, setName] = useState(task?.name ?? "");
  const [prompt, setPrompt] = useState(task?.prompt ?? "");
  const [kind, setKind] = useState<"cron" | "at">(task?.kind ?? "cron");
  const [expression, setExpression] = useState(task?.schedule.expression ?? "0 9 * * *");
  const [runAt, setRunAt] = useState(task?.schedule.runAt ? new Date(task.schedule.runAt).toLocaleString("sv-SE").replace(" ", "T").slice(0, 16) : "");
  const [timezone, setTimezone] = useState(task?.schedule.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [target, setTarget] = useState(task?.target.kind === "existing_session" ? "session" : "new");
  const [sessionId, setSessionId] = useState(task?.target.sessionId ?? currentSessionId ?? "");
  const [projectId, setProjectId] = useState(task ? task.target.projectId ?? "" : currentProjectId ?? current?.project?.id ?? "");
  const [executionMode, setExecutionMode] = useState<"local" | "worktree">(task?.target.execution?.executionMode ?? "local");
  const [baseRef, setBaseRef] = useState(task?.target.execution?.baseRef ?? "");
  const [modelId, setModelId] = useState(task?.model.modelId ?? models[0]?.id ?? "");
  const [reasoning, setReasoning] = useState<ModelReasoningSelection | "">(task?.model.reasoningSelection ?? "");
  const [approvalMode, setApprovalMode] = useState<"manual" | "auto_review">(task?.approvalMode ?? "auto_review");
  const [status, setStatus] = useState<"ACTIVE" | "PAUSED">(task?.status ?? "ACTIVE");
  const [preview, setPreview] = useState<string[]>([]);
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const reasoningSelections = models.find((model) => model.id === modelId)?.reasoning?.selections ?? [];
  const schedule = () => kind === "cron"
    ? { expression, timezone }
    : { runAt: new Date(runAt).toISOString(), timezone };
  const existingTarget = task?.target.sessionId === sessionId;
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setPreviewError("");
    try {
      const selectedModel = { modelId, ...(reasoning ? { reasoningSelection: reasoning } : {}) };
      const execution = { executionMode, ...(baseRef && executionMode === "worktree" ? { baseRef } : {}) };
      const binding = target === "session" ? { sessionId } : { projectId: projectId || null, execution };
      if (task) {
        // Submit only changed fields. A time edit must not reset model, target or permissions.
        const changes: NonNullable<AutomationRequest["changes"]> = {};
        if (name !== task.name) changes.name = name;
        if (prompt !== task.prompt) changes.prompt = prompt;
        const originalTime = task.schedule.runAt ? new Date(task.schedule.runAt).toLocaleString("sv-SE").replace(" ", "T").slice(0, 16) : "";
        if (kind !== task.kind || timezone !== task.schedule.timezone || (kind === "cron" ? expression !== task.schedule.expression : runAt !== originalTime)) {
          changes.schedule = schedule(); changes.kind = kind;
        }
        if (modelId !== task.model.modelId || reasoning !== (task.model.reasoningSelection ?? "")) changes.model = selectedModel;
        if (approvalMode !== task.approvalMode) changes.approvalMode = approvalMode;
        if (status !== task.status) changes.status = status;
        if ((target === "session" && (!existingTarget || task.target.kind !== "existing_session"))
          || (target === "new" && (task.target.kind !== "new_session" || (task.target.projectId ?? "") !== projectId
            || task.target.execution?.executionMode !== executionMode || (task.target.execution?.baseRef ?? "") !== baseRef))) changes.target = binding;
        if (!Object.keys(changes).length) { setPreviewError("配置没有变化。"); return; }
        await onSave({ mode: "update", id: task.id, expectedRevision: task.revision, changes });
      } else {
        await onSave({ mode: "create", kind, name, prompt, schedule: schedule(), status, model: selectedModel, approvalMode, ...binding });
      }
    } catch (error) { setPreviewError(error instanceof RangeError ? "请选择有效的执行时间。" : userFacingError(error)); }
  };
  const showPreview = async () => {
    setPreviewing(true); setPreviewError("");
    try {
      const result = await window.eidosRuntime.automation({ command: { mode: "preview", kind, schedule: schedule() } });
      setPreview(result.nextRuns ?? []);
    } catch (error) { setPreviewError(error instanceof RangeError ? "请选择有效的执行时间。" : userFacingError(error)); }
    finally { setPreviewing(false); }
  };
  return <form className="automation-form" onSubmit={(event) => void submit(event)}>
    <h2>{task ? "编辑任务" : "新建任务"}</h2>
    <fieldset disabled={busy} className="automation-form-controls">
    <label>名称<input required maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>任务内容<textarea required rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="说明每次要完成的工作，以及何时停止。" /></label>
    <div className="automation-fields">
      <label>时间类型<select value={kind} onChange={(event) => { setKind(event.target.value as "at" | "cron"); setPreview([]); }}><option value="cron">周期执行</option><option value="at">执行一次</option></select></label>
      <label>时区<input required value={timezone} onChange={(event) => { setTimezone(event.target.value); setPreview([]); }} /></label>
    </div>
    {kind === "cron" ? <label>执行规则<select value={["*/10 * * * *", "*/30 * * * *", "0 * * * *", "0 9 * * *", "0 9 * * 1-5"].includes(expression) ? expression : "custom"} onChange={(event) => { setExpression(event.target.value === "custom" ? "0 9 * * 1" : event.target.value); setPreview([]); }}>
      <option value="*/10 * * * *">每 10 分钟</option><option value="*/30 * * * *">每 30 分钟</option><option value="0 * * * *">每 1 小时</option><option value="0 9 * * *">每天 09:00</option><option value="0 9 * * 1-5">周一至周五 09:00</option><option value="custom">自定义日历时间</option>
    </select><input aria-label="Cron 表达式" required value={expression} onChange={(event) => { setExpression(event.target.value); setPreview([]); }} /></label>
      : <label>执行时间（本机时间）<input type="datetime-local" required value={runAt} onChange={(event) => { setRunAt(event.target.value); setPreview([]); }} /></label>}
    <div><Button size="small" disabled={previewing || busy} onClick={() => void showPreview()}>预览执行时间</Button>
      {preview.length > 0 && <ol className="automation-preview">{preview.map((time) => <li key={time}>{new Date(time).toLocaleString(undefined, { timeZone: timezone })}</li>)}</ol>}</div>
    <label>执行会话<select value={target} onChange={(event) => setTarget(event.target.value)}><option value="new">每次新建会话</option><option value="session">已有会话</option></select></label>
    {target === "session" ? <label>目标会话<select required value={sessionId} onChange={(event) => setSessionId(event.target.value)}><option value="">选择会话</option>{sessionId && !sessions.some((session) => session.id === sessionId) && <option value={sessionId}>当前绑定的会话</option>}{sessions.map((session) => <option key={session.id} value={session.id}>{session.title ?? "未命名会话"}</option>)}</select></label>
      : <><label>所属项目<select value={projectId} onChange={(event) => { setProjectId(event.target.value); setExecutionMode("local"); }}><option value="">无项目</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
        {projectId && <label>执行环境<select value={executionMode} onChange={(event) => setExecutionMode(event.target.value as "local" | "worktree")}><option value="local">本地目录</option><option value="worktree">新建工作树（Git 项目）</option></select></label>}
        {executionMode === "worktree" && <label>起始分支<input value={baseRef} onChange={(event) => setBaseRef(event.target.value)} placeholder="默认分支" /></label>}</>}
    <div className="automation-fields"><label>模型<select required value={modelId} onChange={(event) => { setModelId(event.target.value); setReasoning(""); }}><option value="">选择模型</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
      <label>思考强度<select value={reasoning} onChange={(event) => setReasoning(event.target.value as ModelReasoningSelection | "")}><option value="">模型默认</option>{reasoning && !reasoningSelections.includes(reasoning) && <option value={reasoning}>{reasoning}（配置已变更）</option>}{reasoningSelections.map((value) => <option key={value} value={value}>{value}</option>)}</select></label></div>
    <div className="automation-fields"><label>审批模式<select value={approvalMode} onChange={(event) => setApprovalMode(event.target.value as "manual" | "auto_review")}><option value="auto_review">替我审批</option><option value="manual">请求审批</option></select></label>
      <label>状态<select value={status} onChange={(event) => setStatus(event.target.value as "ACTIVE" | "PAUSED")}><option value="ACTIVE">启用</option><option value="PAUSED">暂停</option></select></label></div>
    {previewError && <p role="alert" className="approval-error">{previewError}</p>}
    <Button type="submit" variant="primary" disabled={busy}>{busy ? "正在保存…" : "保存任务"}</Button>
    </fieldset>
  </form>;
}
