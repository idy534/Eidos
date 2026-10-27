import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { Automation, AutomationDesktopRequest } from "../../../../shared/automation.js";
import type { ApprovalMode, ModelOption, ModelReasoningSelection, Project, Session } from "../../contracts.js";
import type { ToastType } from "../ToastContainer.js";
import { ModelSelector } from "../ModelSelector.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { userFacingError } from "../../session-state.js";
import { AutomationTimeError, COMMON_TIMEZONE_GROUPS, isCommonTimezone, automationDateInput, formatPreviewTime, initialOneTimeChoice, oneTimeOptions } from "./automation-time.js";

export interface InitialAutomationValues {
  name?: string | undefined;
  prompt?: string | undefined;
  expression?: string | undefined;
  kind?: "cron" | "at" | undefined;
  approvalMode?: "manual" | "auto_review" | undefined;
}

interface Props {
  task?: Automation | undefined;
  initialValues?: InitialAutomationValues | undefined;
  projects: Project[];
  sessions: Session[];
  models: ModelOption[];
  currentSessionId?: string | undefined;
  currentProjectId?: string | undefined;
  busy: boolean;
  onSave: (request: AutomationDesktopRequest, confirmation?: "full-access-v1") => Promise<void>;
  onCancel?: (() => void) | undefined;
  onShowToast?: ((message: string, type?: ToastType) => void) | undefined;
}

export function AutomationForm({ task, initialValues, projects, sessions, models, currentSessionId, currentProjectId, busy, onSave, onCancel, onShowToast }: Props) {
  const timeErrorId = useId();
  const current = sessions.find((session) => session.id === currentSessionId);
  const [name, setName] = useState(task?.name ?? initialValues?.name ?? "");
  const [prompt, setPrompt] = useState(task?.prompt ?? initialValues?.prompt ?? "");
  const [kind, setKind] = useState<"cron" | "at">(task?.kind ?? initialValues?.kind ?? "cron");
  const [expression, setExpression] = useState(task?.schedule.expression ?? initialValues?.expression ?? "0 9 * * *");
  const [initialTime] = useState(() => {
    const timezone = task?.schedule.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
    const instant = task?.schedule.runAt;
    return { timezone, runAt: instant ? automationDateInput(instant, timezone) : "",
      choice: instant ? initialOneTimeChoice(instant, timezone) : "" };
  });
  const [runAt, setRunAt] = useState(initialTime.runAt);
  const [timezone, setTimezone] = useState(initialTime.timezone);
  const [timeChoice, setTimeChoice] = useState(initialTime.choice);
  const [target, setTarget] = useState(task?.target.kind === "existing_session" ? "session" : "new");
  const [sessionId, setSessionId] = useState(task?.target.sessionId ?? currentSessionId ?? "");
  const [projectId, setProjectId] = useState(task ? task.target.projectId ?? "" : currentProjectId ?? current?.project?.id ?? "");
  const [executionMode, setExecutionMode] = useState<"local" | "worktree">(task?.target.execution?.executionMode ?? "local");
  const [baseRef, setBaseRef] = useState(task?.target.execution?.baseRef ?? "");
  const [modelId, setModelId] = useState(task?.model.modelId ?? models[0]?.id ?? "");
  const [reasoning, setReasoning] = useState<ModelReasoningSelection | "">(task?.model.reasoningSelection ?? "");
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>(task?.approvalMode ?? initialValues?.approvalMode ?? "auto_review");
  const [showFullAccessDialog, setShowFullAccessDialog] = useState(false);
  const [fullAccessConfirmed, setFullAccessConfirmed] = useState(false);
  const [preview, setPreview] = useState<string[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [showPreviewPopover, setShowPreviewPopover] = useState(false);
  const popoverAnchorRef = useRef<HTMLDivElement>(null);
  const isCustomTimezone = Boolean(timezone && !isCommonTimezone(timezone));
  const timeResolution = useMemo(() => {
    if (kind !== "at" || !runAt) return { options: [], error: "" };
    try { return { options: oneTimeOptions(runAt, timezone), error: "" }; }
    catch (error) { return { options: [], error: (error as Error).message }; }
  }, [kind, runAt, timezone]);
  const timeChanged = runAt !== initialTime.runAt || timezone !== initialTime.timezone || timeChoice !== initialTime.choice;
  const schedule = () => {
    if (kind === "cron") return { expression, timezone };
    if (task?.kind === "at" && !timeChanged) return { runAt: task.schedule.runAt!, timezone };
    if (timeResolution.error) throw new AutomationTimeError(timeResolution.error);
    const option = timeResolution.options.length === 1 ? timeResolution.options[0]
      : timeResolution.options.find((value) => value.instant === timeChoice);
    if (!option) throw new AutomationTimeError("请选择有效的执行时间；时间重复时，请选择对应的 UTC 偏移。");
    return { runAt: option.instant, timezone };
  };
  const existingTarget = task?.target.sessionId === sessionId;
  const save = (request: AutomationDesktopRequest) => fullAccessConfirmed && approvalMode === "full_access"
    ? onSave(request, "full-access-v1") : onSave(request);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (showFullAccessDialog) return;
    if (!modelId) { onShowToast?.("请选择模型。", "error"); return; }
    try {
      const selectedModel = { modelId, ...(reasoning ? { reasoningSelection: reasoning } : {}) };
      const execution = { executionMode, ...(baseRef && executionMode === "worktree" ? { baseRef } : {}) };
      const binding = target === "session" ? { sessionId } : { projectId: projectId || null, execution };
      if (task) {
        // Submit only changed fields. A time edit must not reset model, target or permissions.
        const changes: NonNullable<AutomationDesktopRequest["changes"]> = {};
        if (name !== task.name) changes.name = name;
        if (prompt !== task.prompt) changes.prompt = prompt;
        if (kind !== task.kind || timezone !== initialTime.timezone || (kind === "cron" ? expression !== task.schedule.expression : timeChanged)) {
          changes.schedule = schedule(); changes.kind = kind;
        }
        if (modelId !== task.model.modelId || reasoning !== (task.model.reasoningSelection ?? "")) changes.model = selectedModel;
        if (approvalMode !== task.approvalMode) changes.approvalMode = approvalMode;
        if ((target === "session" && (!existingTarget || task.target.kind !== "existing_session"))
          || (target === "new" && (task.target.kind !== "new_session" || (task.target.projectId ?? "") !== projectId
            || task.target.execution?.executionMode !== executionMode || (task.target.execution?.baseRef ?? "") !== baseRef))) changes.target = binding;
        if (!Object.keys(changes).length) { onShowToast?.("配置没有变化。", "info"); return; }
        await save({ mode: "update", id: task.id, expectedRevision: task.revision, changes });
      } else {
        await save({ mode: "create", kind, name, prompt, schedule: schedule(), status: "ACTIVE", model: selectedModel, approvalMode, ...binding });
      }
    } catch (error) { onShowToast?.(error instanceof AutomationTimeError ? error.message : userFacingError(error), "error"); }
  };
  const resetPreview = () => {
    setShowPreviewPopover(false);
    setPreview([]);
  };
  const togglePreview = async () => {
    if (showPreviewPopover) {
      setShowPreviewPopover(false);
      return;
    }
    setPreviewing(true);
    try {
      const result = await window.eidosRuntime.automation({ command: { mode: "preview", kind, schedule: schedule() } });
      const runs = result.nextRuns ?? [];
      setPreview(runs);
      setShowPreviewPopover(true);
    } catch (error) { onShowToast?.(error instanceof AutomationTimeError ? error.message : userFacingError(error), "error"); }
    finally { setPreviewing(false); }
  };
  useEffect(() => {
    if (!showPreviewPopover) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (popoverAnchorRef.current && !popoverAnchorRef.current.contains(event.target as Node)) {
        setShowPreviewPopover(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowPreviewPopover(false);
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [showPreviewPopover]);
  const previewPopoverNode = (
    <div className="automation-preview-popover-anchor" ref={popoverAnchorRef}>
      <Button
        type="button"
        size="small"
        variant="secondary"
        className="automation-preview-btn"
        disabled={previewing || busy}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void togglePreview();
        }}
      >
        预览执行时间
      </Button>
      {showPreviewPopover && (
        <div className="automation-preview-popover" role="dialog" aria-label="执行时间预览">
          <div className="automation-preview-popover-header">
            <span className="automation-preview-popover-title">未来执行时间</span>
            <span className="automation-preview-popover-tz">{timezone}</span>
          </div>
          {preview.length > 0 ? (
            <ol className="automation-preview-popover-list">
              {preview.map((time, index) => (
                <li key={time} className="automation-preview-popover-item">
                  <span className="automation-preview-popover-num">{index + 1}</span>
                  <span className="automation-preview-popover-text">{formatPreviewTime(time, timezone)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="automation-preview-empty">未获取到未来执行时间</p>
          )}
        </div>
      )}
    </div>
  );
  return <form className="automation-form" onSubmit={(event) => void submit(event)}>
    <div className="automation-form-header">
      <div>
        <h2>{task ? "编辑任务" : "新建任务"}</h2>
        <p className="automation-form-subtitle">{task ? `修改定时任务配置与执行策略` : "配置并创建一个新的定时自动化任务"}</p>
      </div>
    </div>

    <fieldset disabled={busy} className="automation-form-controls">
      <div className="automation-form-card">
        <h3 className="automation-card-title">基本信息</h3>
        <label>名称<input required maxLength={80} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>任务内容<textarea required rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="说明每次要完成的工作，以及何时停止。" /></label>
      </div>

      <div className="automation-form-card">
        <h3 className="automation-card-title">执行计划</h3>
        <div className="automation-fields">
          <label>时间类型<select value={kind} onChange={(event) => { setKind(event.target.value as "at" | "cron"); resetPreview(); }}><option value="cron">周期执行</option><option value="at">执行一次</option></select></label>
          <label>时区<select required value={timezone} onChange={(event) => { setTimezone(event.target.value); setTimeChoice(""); resetPreview(); }}>
            {isCustomTimezone && (
              <optgroup label="当前设置">
                <option value={timezone}>{timezone}</option>
              </optgroup>
            )}
            {COMMON_TIMEZONE_GROUPS.map((group) => (
              <optgroup key={group.region} label={group.region}>
                {group.zones.map((zone) => (
                  <option key={zone.value} value={zone.value}>
                    {zone.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select></label>
        </div>
        {kind === "cron" ? (
          <label>
            <span>执行规则</span>
            <div className="automation-rule-row">
              <select
                aria-label="预设执行规则"
                className="automation-rule-preset"
                value={["*/10 * * * *", "*/30 * * * *", "0 * * * *", "0 9 * * *", "0 9 * * 1-5"].includes(expression) ? expression : "custom"}
                onChange={(event) => {
                  setExpression(event.target.value === "custom" ? "0 9 * * 1" : event.target.value);
                  resetPreview();
                }}
              >
                <option value="*/10 * * * *">每 10 分钟</option>
                <option value="*/30 * * * *">每 30 分钟</option>
                <option value="0 * * * *">每 1 小时</option>
                <option value="0 9 * * *">每天 09:00</option>
                <option value="0 9 * * 1-5">周一至周五 09:00</option>
                <option value="custom">自定义日历时间</option>
              </select>
              <input
                aria-label="Cron 表达式"
                className="automation-rule-input"
                required
                value={expression}
                onChange={(event) => {
                  setExpression(event.target.value);
                  resetPreview();
                }}
              />
              {previewPopoverNode}
            </div>
          </label>
        ) : (
          <>
            <label>
              <span>执行时间（所选时区）</span>
              <div className="automation-rule-row">
                <input
                  type="datetime-local"
                  required
                  value={runAt}
                  aria-invalid={Boolean(timeResolution.error)}
                  aria-describedby={timeResolution.error ? timeErrorId : undefined}
                  onChange={(event) => {
                    setRunAt(event.target.value);
                    setTimeChoice("");
                    resetPreview();
                  }}
                />
                {previewPopoverNode}
              </div>
            </label>
            {timeResolution.error && <p id={timeErrorId} role="alert">{timeResolution.error}</p>}
            {timeResolution.options.length > 1 && (
              <label>
                这个时间出现两次，请选择执行时刻
                <select
                  required
                  value={timeChoice}
                  onChange={(event) => {
                    setTimeChoice(event.target.value);
                    resetPreview();
                  }}
                >
                  <option value="">请选择执行时刻</option>
                  {timeResolution.options.map((option, index) => (
                    <option key={option.instant} value={option.instant}>
                      第 {index + 1} 次（UTC{option.offset}）
                    </option>
                  ))}
                </select>
              </label>
            )}
          </>
        )}
      </div>

      <div className="automation-form-card">
        <h3 className="automation-card-title">执行会话与环境</h3>
        <div className="automation-fields">
          <label>执行会话<select value={target} onChange={(event) => setTarget(event.target.value)}><option value="new">每次新建会话</option><option value="session">已有会话</option></select></label>
          {target === "session" ? (
            <label>目标会话<select required value={sessionId} onChange={(event) => setSessionId(event.target.value)}><option value="">选择会话</option>{sessionId && !sessions.some((session) => session.id === sessionId) && <option value={sessionId}>当前绑定的会话</option>}{sessions.map((session) => <option key={session.id} value={session.id}>{session.title ?? "未命名会话"}</option>)}</select></label>
          ) : (
            <label>所属项目<select value={projectId} onChange={(event) => { setProjectId(event.target.value); setExecutionMode("local"); }}><option value="">无项目</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
          )}
        </div>
        {target === "new" && projectId && (
          <div className="automation-fields">
            <label>执行环境<select value={executionMode} onChange={(event) => setExecutionMode(event.target.value as "local" | "worktree")}><option value="local">本地目录</option><option value="worktree">新建工作树（Git 项目）</option></select></label>
            {executionMode === "worktree" && <label>起始分支<input value={baseRef} onChange={(event) => setBaseRef(event.target.value)} placeholder="默认分支" /></label>}
          </div>
        )}
      </div>

      <div className="automation-form-card">
        <h3 className="automation-card-title">模型与权限</h3>
        <div className="automation-fields">
          <div className="model-selector-field"><span>模型与思考强度</span>
            <ModelSelector models={models} selectedModelId={modelId} selection={reasoning || undefined} disabled={busy}
              onModelChange={(id) => { setModelId(id); setReasoning(""); }} onChange={(value) => setReasoning(value ?? "")} />
          </div>
          <label>审批模式<select value={approvalMode} onChange={(event) => {
            const next = event.target.value as ApprovalMode;
            if (next === approvalMode) return;
            if (next === "full_access") setShowFullAccessDialog(true);
            else { setApprovalMode(next); setFullAccessConfirmed(false); }
          }}><option value="auto_review">替我审批</option><option value="manual">请求审批</option><option value="full_access">完全访问</option></select></label>
        </div>
      </div>

      <div className="automation-form-actions">
        <Button type="submit" variant="primary" disabled={busy}>{busy ? "正在保存…" : "保存任务"}</Button>
        {onCancel && (
          <Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>
            取消
          </Button>
        )}
      </div>
    </fieldset>
    <ConfirmDialog open={showFullAccessDialog} title="开启完全访问？" isDestructive
      confirmLabel="开启完全访问" cancelLabel="取消"
      description={<div className="automation-full-access-warning">
        <p>Eidos 将不再逐项请求批准</p>
        <p>此计划今后的每次执行都将以当前 macOS 用户的权限读写文件、运行命令和访问网络。操作可能造成数据丢失、凭据泄露或系统设置变化，也可能修改 Eidos 自身的数据。请仅在信任该定时任务时开启。</p>
      </div>}
      onConfirm={() => { setApprovalMode("full_access"); setFullAccessConfirmed(true); setShowFullAccessDialog(false); }}
      onCancel={() => setShowFullAccessDialog(false)} />
  </form>;
}
