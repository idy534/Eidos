import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AgentDefinition, AgentDefinitionDocument, AgentModelSelection } from "../../../../shared/collaboration.generated.js";
import type { ModelOption } from "../../contracts.js";
import { Button } from "../Button.js";
import { ModelSelector } from "../ModelSelector.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { userFacingError } from "../../session-state.js";
import { MarkdownContent } from "../MarkdownContent.js";
import { CodeIcon, CompressIcon, ExpandIcon, PreviewIcon } from "../MenuIcons.js";

export const agentRoleLabels: Record<string, string> = { default: "通用", explorer: "调查", worker: "工作" };
const emptyDefinition: AgentDefinition = { schemaVersion: 1, name: "", description: "", instructions: "", baseRole: "default", enabled: true };
const reasoningLabels = { none: "关闭思考", thinking: "开启思考", low: "低", medium: "中", high: "高", max: "最高" };

export function AgentRoleIcon({ role, size = 18 }: { role: string; size?: number }) {
  if (role === "worker") {
    return (
      <svg viewBox="0 0 20 20" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="m6 7-4 3 4 3" />
        <path d="m14 7 4 3-4 3" />
        <path d="m11 5-2 10" />
      </svg>
    );
  }
  if (role === "explorer") {
    return (
      <svg viewBox="0 0 20 20" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="10" cy="10" r="7.5" />
        <polygon points="13.5,6.5 11.5,11.5 6.5,13.5 8.5,8.5" fill="currentColor" fillOpacity="0.25" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 20 20" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3.5" y="5" width="13" height="11" rx="3" />
      <circle cx="7.5" cy="10.5" r="1.2" fill="currentColor" />
      <circle cx="12.5" cy="10.5" r="1.2" fill="currentColor" />
      <path d="M10 2v3" />
    </svg>
  );
}

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
  const [expandedMd, setExpandedMd] = useState(false);
  const [viewMode, setViewMode] = useState<"edit" | "preview">("edit");
  const nameRef = useRef<HTMLInputElement>(null);
  const instructionsRef = useRef<HTMLTextAreaElement>(null);
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
    if (instructionsError && viewMode === "preview") {
      setViewMode("edit");
    }
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
    <>
      <div className="modal-backdrop agent-editor-backdrop" aria-hidden={expandedMd}>
        <form
          ref={dialogRef}
          noValidate
          className="modal-dialog agent-editor"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          onSubmit={(event) => { event.preventDefault(); event.stopPropagation(); void save(); }}
          onKeyDown={(event) => {
            if (expandedMd) return;
            if (event.key === "Escape") {
              event.stopPropagation();
              if (!busy) onClose();
            }
          if (event.key !== "Tab") return;
          const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']") ?? []);
          const panel = (event.target as HTMLElement).closest?.(".model-selector__panel");
          if (panel) {
            const items = Array.from(panel.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled])"));
            const trigger = dialogRef.current?.querySelector<HTMLButtonElement>(".model-selector__trigger");
            if (event.shiftKey && (document.activeElement === items[0] || document.activeElement?.getAttribute("tabindex") === "-1")) {
              event.preventDefault(); trigger?.focus();
            } else if (!event.shiftKey && document.activeElement === items.at(-1)) {
              event.preventDefault(); (instructionsRef.current ?? controls[controls.indexOf(trigger!) + 1])?.focus();
            }
            return;
          }
          const first = controls[0]; const last = controls.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }}
      >
        <header className="modal-header agent-editor-header">
          <div className={`agent-editor-header__icon agent-role-avatar--${draft.baseRole ?? "default"}`} aria-hidden="true">
            <AgentRoleIcon role={draft.baseRole ?? "default"} size={22} />
          </div>
          <div className="agent-editor-header__text">
            <h2 id={titleId}>{original ? "编辑智能体" : "创建智能体"}</h2>
            <p className="modal-subtitle">设置智能体的职责、基础角色、模型与执行指令。</p>
          </div>
        </header>

        <div className="modal-body agent-editor__body">
          <fieldset disabled={busy} className="agent-editor__fieldset">
            {/* 紧凑基本配置区块 */}
            <div className="agent-editor__section agent-editor__section--compact">
              <div className="agent-editor__grid-row">
                {/* 名称 */}
                <label className="agent-editor__field agent-editor__field--flex-grow">
                  <span className="agent-editor__label-row">
                    <span className="agent-editor__label-title">名称 <span className="field-required" aria-hidden="true">*</span></span>
                  </span>
                  <div className="agent-editor__input-affix">
                    <span className="agent-editor__affix-text" aria-hidden="true">@</span>
                    <input
                      ref={nameRef}
                      aria-label="名称"
                      required
                      maxLength={64}
                      value={draft.name}
                      readOnly={Boolean(original)}
                      placeholder="例如 luna-work"
                      className="agent-editor__text-input"
                      onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                    />
                  </div>
                  {attempted && nameError && <span className="setting-field-error" role="alert">{nameError}</span>}
                </label>

                {/* 基础角色 */}
                <label className="agent-editor__field agent-editor__field--role">
                  <span className="agent-editor__label-row">
                    <span className="agent-editor__label-title">基础角色</span>
                  </span>
                  <div className="model-select-control model-select-control--with-prefix agent-role-select-control">
                    <span className="model-select-control__prefix" aria-hidden="true">
                      <AgentRoleIcon role={draft.baseRole ?? "default"} size={16} />
                    </span>
                    <select
                      aria-label="基础角色"
                      value={draft.baseRole ?? "default"}
                      onChange={(event) => setDraft({ ...draft, baseRole: event.target.value as NonNullable<AgentDefinition["baseRole"]> })}
                    >
                      {Object.entries(agentRoleLabels).map(([value, label]) => (
                        <option value={value} key={value}>{label}（{value}）</option>
                      ))}
                    </select>
                    <span className="model-select-control__chevron" aria-hidden="true">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="m6 9 6 6 6-6" />
                      </svg>
                    </span>
                  </div>
                </label>
              </div>

              {/* 用途说明 */}
              <label className="agent-editor__field">
                <span className="agent-editor__label-row">
                  <span className="agent-editor__label-title">用途说明 <span className="field-required" aria-hidden="true">*</span></span>
                </span>
                <input
                  aria-label="用途说明"
                  required
                  maxLength={1000}
                  value={draft.description}
                  placeholder="说明父任务在什么情况下使用它，例如：负责定向代码实现与验证"
                  className="agent-editor__text-input"
                  onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                />
                {attempted && descriptionError && <span className="setting-field-error" role="alert">{descriptionError}</span>}
              </label>

              {/* 模型选择 */}
              <div className="agent-editor__model-selection-group">
                <div className="agent-editor__model-mode-row">
                  <span className="agent-editor__label-title">模型选择</span>
                  <div className="agent-editor__model-controls">
                    <div className="agent-editor__segmented-bar" role="group" aria-label="模型选择">
                      <label className={`agent-editor__segment ${!configured ? "agent-editor__segment--active" : ""}`}>
                        <input
                          type="radio"
                          name={`${titleId}-model`}
                          aria-label="继承父任务"
                          checked={!configured}
                          onChange={() => { setConfigured(false); setDraft({ ...draft, model: null }); }}
                          className="agent-editor__segment-input"
                        />
                        <span className="agent-editor__segment-pill">
                          继承父任务
                        </span>
                      </label>
                      <label className={`agent-editor__segment ${configured ? "agent-editor__segment--active" : ""}`}>
                        <input
                          type="radio"
                          name={`${titleId}-model`}
                          aria-label="指定"
                          checked={configured}
                          onChange={() => { setConfigured(true); chooseModel(selectableModels[0]?.id ?? ""); }}
                          className="agent-editor__segment-input"
                        />
                        <span className="agent-editor__segment-pill">
                          指定
                        </span>
                      </label>
                    </div>

                    <div
                      className={`agent-editor__model-animator ${configured ? "agent-editor__model-animator--open" : ""}`}
                      aria-hidden={!configured}
                    >
                      <div className="agent-editor__model-animator-inner">
                        <ModelSelector
                          models={selectableModels}
                          selectedModelId={model?.id}
                          selection={selection?.reasoningSelection ?? undefined}
                          disabled={!configured || busy || modelLoading}
                          onModelChange={chooseModel}
                          onChange={updateReasoning}
                        />
                      </div>
                    </div>
                  </div>
                </div>

                {configured && selection?.configId && !model && (
                  <small className="setting-field-error">已保存的模型配置不可用或提供商不匹配，请重新选择。</small>
                )}
                {configured && selection?.reasoningSelection && (
                  <div className="agent-editor__reasoning-action">
                    <Button variant="ghost" size="small" onClick={() => updateReasoning("")}>
                      使用模型默认思考设置
                    </Button>
                  </div>
                )}

                {!configured && selection?.reasoningSelection && (
                  <p className="agent-editor__hint">
                    当前配置继承父任务模型，并单独指定思考强度：{reasoningLabels[selection.reasoningSelection]}。
                    <Button variant="ghost" size="small" onClick={() => updateReasoning("")}>一并继承思考设置</Button>
                  </p>
                )}
                {modelLoading && <p role="status" className="agent-editor__status-hint">正在读取模型配置…</p>}
                {modelError && <p role="alert" className="setting-field-error">{modelError}</p>}
                {attempted && modelValidationError && <p role="alert" className="setting-field-error">{modelValidationError}</p>}
                {!modelLoading && !models.length && <p className="agent-editor__hint">你还没有配置模型。智能体可以先继承父任务模型。</p>}
              </div>
            </div>

            {/* Agent.md 格式指令区块 */}
            <div className="agent-editor__section agent-editor__section--instructions">
              <div className="agent-editor__section-header-row">
                <div className="agent-editor__title-group">
                  <span className="agent-editor__label-title agent-editor__label-title--code">
                    Agent.md <span className="field-required" aria-hidden="true">*</span>
                  </span>
                  <span className="agent-editor__bytes-badge">
                    {new TextEncoder().encode(draft.instructions).length} / 48 KiB
                  </span>
                </div>

                <div className="agent-editor__instructions-toolbar">
                  <div className="agent-editor__segmented-bar agent-editor__segmented-bar--sm" role="tablist" aria-label="查看方式">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={viewMode === "edit"}
                      className={`agent-editor__toolbar-tab ${viewMode === "edit" ? "agent-editor__toolbar-tab--active" : ""}`}
                      onClick={() => setViewMode("edit")}
                      title="查看与编辑源码"
                    >
                      <CodeIcon className="agent-editor__tab-icon" />
                      <span>源码</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={viewMode === "preview"}
                      className={`agent-editor__toolbar-tab ${viewMode === "preview" ? "agent-editor__toolbar-tab--active" : ""}`}
                      onClick={() => setViewMode("preview")}
                      title="预览 Markdown 效果"
                    >
                      <PreviewIcon className="agent-editor__tab-icon" />
                      <span>预览</span>
                    </button>
                  </div>

                  <button
                    type="button"
                    className="agent-editor__icon-btn"
                    onClick={() => setExpandedMd(true)}
                    title="放大编辑"
                    aria-label="放大编辑"
                  >
                    <ExpandIcon />
                  </button>
                </div>
              </div>

              {viewMode === "edit" ? (
                <label className="agent-editor__field agent-editor__field--instructions">
                  <textarea
                    ref={instructionsRef}
                    aria-label="Agent.md"
                    required
                    maxLength={49152}
                    rows={9}
                    value={draft.instructions}
                    placeholder={"# 角色定位\n你负责范围明确的实现、修复与验证。\n\n## 工作约束\n- 先探索并理解已有代码架构\n- 每次改动保持定向测试覆盖\n- 报告关键证据与改动点"}
                    className="agent-editor__textarea"
                    onChange={(event) => setDraft({ ...draft, instructions: event.target.value })}
                  />
                </label>
              ) : (
                <div className="agent-editor__preview-wrapper">
                  {draft.instructions.trim() ? (
                    <div className="agent-editor__preview-body eidos-chat-prose" tabIndex={0} role="region" aria-label="Agent.md 预览">
                      <MarkdownContent content={draft.instructions} />
                    </div>
                  ) : (
                    <div className="agent-editor__preview-empty">
                      <p>暂无内容，请切换至「源码」编写 Markdown 格式的 Agent 指令。</p>
                    </div>
                  )}
                </div>
              )}
              {attempted && instructionsError && <span className="setting-field-error" role="alert">{instructionsError}</span>}
            </div>
          </fieldset>
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>

        <footer className="modal-footer agent-editor__footer">
          <Button variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? "正在保存…" : original ? "保存修改" : referenceAfterSave ? "创建并引用" : "创建智能体"}
          </Button>
        </footer>
      </form>
    </div>
    {expandedMd && (
      <AgentMdExpandedDialog
        instructions={draft.instructions}
        viewMode={viewMode}
        onChangeInstructions={(instructions) => setDraft((previous) => ({ ...previous, instructions }))}
        onChangeViewMode={setViewMode}
        onClose={() => {
          setExpandedMd(false);
          setTimeout(() => instructionsRef.current?.focus(), 0);
        }}
      />
    )}
  </>, document.body,
  );
}

function AgentMdExpandedDialog({
  instructions,
  viewMode,
  onChangeInstructions,
  onChangeViewMode,
  onClose,
}: {
  instructions: string;
  viewMode: "edit" | "preview";
  onChangeInstructions(instructions: string): void;
  onChangeViewMode(mode: "edit" | "preview"): void;
  onClose(): void;
}) {
  const titleId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (viewMode === "edit") {
      textareaRef.current?.focus();
    }
  }, [viewMode]);

  return (
    <div
      className="agent-md-expanded-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="agent-md-expanded-dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
          }
        }}
      >
        <header className="agent-md-expanded-header">
          <div className="agent-editor__title-group">
            <span id={titleId} className="agent-editor__label-title agent-editor__label-title--code">
              Agent.md
            </span>
            <span className="agent-editor__bytes-badge">
              {new TextEncoder().encode(instructions).length} / 48 KiB
            </span>
          </div>

          <div className="agent-editor__instructions-toolbar">
            <div className="agent-editor__segmented-bar agent-editor__segmented-bar--sm" role="tablist" aria-label="查看方式">
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === "edit"}
                className={`agent-editor__toolbar-tab ${viewMode === "edit" ? "agent-editor__toolbar-tab--active" : ""}`}
                onClick={() => onChangeViewMode("edit")}
                title="查看与编辑源码"
              >
                <CodeIcon className="agent-editor__tab-icon" />
                <span>源码</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === "preview"}
                className={`agent-editor__toolbar-tab ${viewMode === "preview" ? "agent-editor__toolbar-tab--active" : ""}`}
                onClick={() => onChangeViewMode("preview")}
                title="预览 Markdown 效果"
              >
                <PreviewIcon className="agent-editor__tab-icon" />
                <span>预览</span>
              </button>
            </div>

            <button
              type="button"
              className="agent-editor__icon-btn"
              onClick={onClose}
              title="收起"
              aria-label="收起"
            >
              <CompressIcon />
            </button>
          </div>
        </header>

        <div className="agent-md-expanded-body">
          {viewMode === "edit" ? (
            <textarea
              ref={textareaRef}
              aria-label="Agent.md"
              maxLength={49152}
              value={instructions}
              placeholder={"# 角色定位\n你负责范围明确的实现、修复与验证。\n\n## 工作约束\n- 先探索并理解已有代码架构\n- 每次改动保持定向测试覆盖\n- 报告关键证据与改动点"}
              className="agent-md-expanded-textarea"
              onChange={(event) => onChangeInstructions(event.target.value)}
            />
          ) : (
            <div className="agent-md-expanded-preview-wrapper">
              {instructions.trim() ? (
                <div className="agent-md-expanded-preview-body eidos-chat-prose" tabIndex={0} role="region" aria-label="Agent.md 预览">
                  <MarkdownContent content={instructions} />
                </div>
              ) : (
                <div className="agent-editor__preview-empty">
                  <p>暂无内容，请切换至「源码」编写 Markdown 格式的 Agent 指令。</p>
                </div>
              )}
            </div>
          )}
        </div>

        <footer className="agent-md-expanded-footer">
          <span className="agent-md-expanded-tip">按 Esc 或点击完成返回</span>
          <Button variant="primary" size="small" onClick={onClose}>
            完成
          </Button>
        </footer>
      </div>
    </div>
  );
}
