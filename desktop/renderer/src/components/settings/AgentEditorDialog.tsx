import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AgentDefinition, AgentDefinitionDocument, AgentModelSelection } from "../../../../shared/collaboration.generated.js";
import type { ModelOption } from "../../contracts.js";
import { Button } from "../Button.js";
import { ModelSelector } from "../ModelSelector.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { userFacingError } from "../../session-state.js";

export const agentRoleLabels = { default: "通用", explorer: "代码调查", worker: "实现与验证" };
const emptyDefinition: AgentDefinition = { schemaVersion: 1, name: "", description: "", instructions: "", baseRole: "default", enabled: true };
const reasoningLabels = { none: "关闭思考", thinking: "开启思考", low: "低", medium: "中", high: "高", max: "最高" };

export function AgentEditorDialog({ document: original, initialDefinition, referenceAfterSave = false, onSaved, onClose }: {
  document?: AgentDefinitionDocument | undefined;
  initialDefinition?: AgentDefinition | undefined;
  referenceAfterSave?: boolean;
  onSaved(document: AgentDefinitionDocument): void;
  onClose(): void;
}) {
  const [draft, setDraft] = useState<AgentDefinition>(() => original?.definition ?? initialDefinition ?? emptyDefinition);
  const [configured, setConfigured] = useState(Boolean(draft.model?.configId || draft.model?.providerId));
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelLoading, setModelLoading] = useState(true);
  const [modelError, setModelError] = useState<string>();
  const [error, setError] = useState<string>();
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLFormElement>(null);
  const titleId = useId();
  useDialogFocusLifecycle({ open: true, initialFocusRef: nameRef });
  useEffect(() => {
    let active = true;
    void window.eidosRuntime.listModels().then((result) => {
      if (active) { setModels(result.models); setModelLoading(false); }
    }, (cause) => { if (active) { setModelError(userFacingError(cause)); setModelLoading(false); } });
    return () => { active = false; };
  }, []);
  const selection = draft.model;
  const selectableModels = models.filter((model) => model.supportsToolCall);
  const model = selectableModels.find((model) => model.id === selection?.configId && model.provider === selection?.providerId);
  const nameError = !/^[a-z][a-z0-9_-]{0,63}$/.test(draft.name) ? "名称需要以小写字母开头，并且只包含小写字母、数字、下划线或连字符。"
    : ["default", "worker", "explorer"].includes(draft.name) ? "名称不能与内置智能体重复。"
    : undefined;
  const descriptionError = !draft.description.trim() ? "请填写用途说明。" : undefined;
  const instructionsError = !draft.instructions.trim() ? "请填写智能体指令。"
    : new TextEncoder().encode(draft.instructions).length > 48 * 1024 ? "指令超过 48 KiB，请缩短内容。" : undefined;
  const modelValidationError = configured && (!model || !model.supportsToolCall) ? "请选择已配置且支持工具调用的模型。"
    : configured && selection?.reasoningSelection && !model?.reasoning?.selections.includes(selection.reasoningSelection) ? "所选模型不支持这个思考档位，请重新选择。" : undefined;
  function chooseModel(configId: string) {
    const chosen = selectableModels.find((option) => option.id === configId);
    setDraft((previous) => ({ ...previous, model: chosen ? { providerId: chosen.provider, configId: chosen.id } : {} }));
  }
  function updateReasoning(value: string) {
    const next = { ...selection };
    if (value) next.reasoningSelection = value as NonNullable<AgentModelSelection["reasoningSelection"]>;
    else delete next.reasoningSelection;
    setDraft({ ...draft, model: configured || value ? next : null });
  }
  async function save() {
    if (busy) return;
    setAttempted(true); setError(undefined);
    if (nameError || descriptionError || instructionsError || modelValidationError) return;
    setBusy(true);
    try {
      const saved = await window.eidosRuntime.saveAgentDefinition({ definition: draft,
        ...(original ? { source: original.source, expectedVersion: original.version } : {}) });
      onSaved(saved);
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setBusy(false); }
  }
  return createPortal(
    <div className="modal-backdrop agent-editor-backdrop">
      <form ref={dialogRef} noValidate className="modal-dialog agent-editor" role="dialog" aria-modal="true" aria-labelledby={titleId}
        onSubmit={(event) => { event.preventDefault(); event.stopPropagation(); void save(); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.stopPropagation(); if (!busy) onClose(); }
          if (event.key !== "Tab") return;
          const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']") ?? []);
          const panel = (event.target as HTMLElement).closest?.(".model-selector__panel");
          if (panel) {
            const items = Array.from(panel.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled])"));
            const trigger = dialogRef.current?.querySelector<HTMLButtonElement>(".model-selector__trigger");
            if (event.shiftKey && (document.activeElement === items[0] || document.activeElement?.getAttribute("tabindex") === "-1")) {
              event.preventDefault(); trigger?.focus();
            } else if (!event.shiftKey && document.activeElement === items.at(-1)) {
              event.preventDefault(); controls[controls.indexOf(trigger!) + 1]?.focus();
            }
            return;
          }
          const first = controls[0]; const last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }}>
        <header className="modal-header">
          <h2 id={titleId}>{original ? "编辑智能体" : "创建智能体"}</h2>
          <p className="modal-subtitle">设置智能体的职责、模型和执行指令。</p>
        </header>
        <div className="modal-body agent-editor__body">
          <fieldset disabled={busy}>
            <label><span>名称 <span aria-hidden="true">*</span></span>
              <input ref={nameRef} aria-label="名称" required maxLength={64} value={draft.name} readOnly={Boolean(original)} placeholder="例如 luna-work"
                onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
              <small>名称用于引用和调用。创建后如需改名，请复制为新智能体。</small>
              {attempted && nameError && <span className="setting-field-error" role="alert">{nameError}</span>}
            </label>
            <label><span>用途说明 <span aria-hidden="true">*</span></span>
              <input aria-label="用途说明" required maxLength={1000} value={draft.description} placeholder="说明父任务应在什么情况下使用它"
                onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
              {attempted && descriptionError && <span className="setting-field-error" role="alert">{descriptionError}</span>}
            </label>
            <label><span>基础角色</span>
              <select aria-label="基础角色" value={draft.baseRole ?? "default"} onChange={(event) => setDraft({ ...draft, baseRole: event.target.value as NonNullable<AgentDefinition["baseRole"]> })}>
                {Object.entries(agentRoleLabels).map(([value, label]) => <option value={value} key={value}>{label}（{value}）</option>)}
              </select>
              <small>基础角色提供工作职责。智能体沿用父任务的权限和审批模式。</small>
            </label>
            <div className="agent-editor__model-mode" role="group" aria-label="模型选择">
              <span>模型</span>
              <label><input type="radio" name={`${titleId}-model`} checked={!configured} onChange={() => { setConfigured(false); setDraft({ ...draft, model: null }); }} />继承父任务模型</label>
              <label><input type="radio" name={`${titleId}-model`} checked={configured} onChange={() => { setConfigured(true); chooseModel(selectableModels[0]?.id ?? ""); }} />指定模型</label>
            </div>
            {configured && <div className="agent-editor__model-field">
              <span>模型与思考强度</span>
              <ModelSelector models={selectableModels} selectedModelId={model?.id} selection={selection?.reasoningSelection ?? undefined} disabled={busy || modelLoading}
                onModelChange={chooseModel} onChange={updateReasoning} />
              {selection?.configId && !model && <small>已保存的模型配置不可用或提供商不匹配，请重新选择。</small>}
              {selection?.reasoningSelection && <Button variant="ghost" size="small" onClick={() => updateReasoning("")}>使用模型默认思考设置</Button>}
            </div>}
            {!configured && selection?.reasoningSelection && <p className="agent-editor__hint">
              当前配置继承父任务模型，并单独指定思考强度：{reasoningLabels[selection.reasoningSelection]}。
              <Button variant="ghost" size="small" onClick={() => updateReasoning("")}>一并继承思考设置</Button>
            </p>}
            {modelLoading && <p role="status">正在读取模型配置…</p>}
            {modelError && <p role="alert" className="setting-field-error">{modelError}</p>}
            {attempted && modelValidationError && <p role="alert" className="setting-field-error">{modelValidationError}</p>}
            {!modelLoading && !models.length && <p className="agent-editor__hint">你还没有配置模型。智能体可以先继承父任务模型。</p>}
            <label><span>指令 <span aria-hidden="true">*</span></span>
              <textarea aria-label="指令" required maxLength={49152} rows={8} value={draft.instructions}
                placeholder={"你负责完成父任务分配的实现任务。\n你需要先阅读相关源码，并保留已有修改。\n你需要报告修改内容、验证结果和未完成事项。"}
                onChange={(event) => setDraft({ ...draft, instructions: event.target.value })} />
              <small>你可以填写职责、执行约束、任务背景和交付要求。</small>
              {attempted && instructionsError && <span className="setting-field-error" role="alert">{instructionsError}</span>}
            </label>
          </fieldset>
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>
        <footer className="modal-footer agent-editor__footer">
          <Button variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={busy}>{busy ? "正在保存…" : original ? "保存修改" : referenceAfterSave ? "创建并引用" : "创建智能体"}</Button>
        </footer>
      </form>
    </div>, document.body,
  );
}
