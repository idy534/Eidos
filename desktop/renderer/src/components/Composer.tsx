import type { InputReference, InputPrepareRequest } from "../../../shared/input-context.js";
import { canonicalizeInlineText } from "../../../shared/inline-references.js";
import { InputReferenceCards, isInlineReference, isShelfReference, useInputContext, useReferenceActivation } from "./InputContext.js";
import { InlineRichEditor } from "./InlineRichEditor.js";
import { InputPicker, type InputPickerMode, LightbulbIcon } from "./InputPicker.js";
import { forwardRef, useCallback, useState, useEffect, useImperativeHandle, useLayoutEffect, useRef } from "react";
import type { ApprovalMode, ContextUsage, ModelId, ModelReasoningSelection, Run, Session } from "../contracts.js";
import type { ComposerMode } from "../session-state.js";
import { formatContextUsage } from "../context-usage.js";
import { ApprovalModeSelector } from "./ApprovalModeSelector.js";
import { Button } from "./Button.js";
import { ContextIndicator } from "./ContextIndicator.js";
import type { ContextDetails } from "../../../shared/context-management.js";
import { ToastContainer } from "./ToastContainer.js";
import { ModelSelector } from "./ModelSelector.js";
import { Tooltip } from "./Tooltip.js";
import { AgentEditorDialog } from "./settings/AgentEditorDialog.js";

export interface ComposerProps {
  composerMode: ComposerMode;
  activeRun: Run | undefined;
  input: string;
  references?: InputReference[];
  onRemoveReference?: (id: string) => void;
  draftReady?: boolean;
  modelList: import("../contracts.js").ModelListResult | undefined;
  selectedModelId: ModelId | undefined;
  reasoningSelection?: ModelReasoningSelection | undefined;
  workMode?: "execute" | "plan";
  onWorkModeChange?: (mode: "execute" | "plan") => void;
  approvalMode?: ApprovalMode | undefined;
  onApprovalModeChange?: ((mode: ApprovalMode) => void) | undefined;
  contextUsage: ContextUsage | undefined;
  contextRunId?: string | undefined;
  contextDetails?: ContextDetails | undefined;
  modelConfigured: boolean;
  modelLoading: boolean;
  isSubmitting: boolean;
  submitKind: "start" | undefined;
  cancelingRunId: string | undefined;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onModelChange: (id: ModelId) => void;
  onReasoningSelectionChange?: (selection: ModelReasoningSelection | undefined) => void;
  onOpenModelSettings: () => void;
  showContextIndicator?: boolean;
  showSessionContext?: boolean;
  project?: Session["project"] | null;
  projectless?: boolean;
  executionMode?: "local" | "worktree" | undefined;
  branch?: string | null | undefined;
  branches?: string[] | undefined;
  onBranchChange?: ((branch: string) => void) | undefined;
  branchChanging?: boolean;
  onSelectProject?: (() => void) | undefined;
  onLeaveProject?: (() => void) | undefined;
  onExecutionModeChange?: ((mode: "local" | "worktree") => void) | undefined;
}

export const Composer = forwardRef<HTMLTextAreaElement, ComposerProps>(function Composer({
  composerMode,
  activeRun,
  input,
  references = [],
  onRemoveReference,
  draftReady = true,
  modelList,
  selectedModelId,
  reasoningSelection,
  workMode = "execute",
  onWorkModeChange,
  approvalMode = "manual",
  onApprovalModeChange,
  contextUsage,
  contextRunId,
  contextDetails,
  modelConfigured,
  modelLoading,
  isSubmitting,
  submitKind,
  cancelingRunId,
  onInputChange,
  onSubmit,
  onCancel,
  onModelChange,
  onReasoningSelectionChange,
  onOpenModelSettings,
  showContextIndicator = true,
  showSessionContext = true,
  project,
  projectless = false,
  executionMode,
  branch,
  branches,
  onBranchChange,
  branchChanging = false,
  onSelectProject,
  onLeaveProject,
  onExecutionModeChange,
}, forwardedRef) {
  const context = useInputContext();
  const {
    activateReference: activateInlineReference,
    error: inlineActivationError,
    dialogs: inlineActivationDialogs,
  } = useReferenceActivation();
  const [picker, setPicker] = useState<{
    mode: InputPickerMode;
    query: string;
    start?: number;
    end?: number;
    insertRange?: { start: number; end: number };
  }>();
  const [agentCreator, setAgentCreator] = useState<{
    owner: string;
    add(request: InputPrepareRequest, range?: { start: number; end: number }): Promise<void>;
    insertRange?: { start: number; end: number };
  }>();
  const [list, setList] = useState<{ id: string; active: string | undefined }>();
  const [showStatus, setShowStatus] = useState(false);
  const [pendingCaretOffset, setPendingCaretOffset] = useState<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const richEditorRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  const selectionRangeRef = useRef<{ start: number; end: number }>({ start: 0, end: 0 });
  const userSelectedRef = useRef(false);

  const shelfReferences = references.filter(isShelfReference);
  const inlineReferences = references.filter(isInlineReference);
  const hasInlineReferences = inlineReferences.length > 0;
  const activeCanonicalInput = hasInlineReferences
    ? canonicalizeInlineText(input, inlineReferences)
    : input;

  const focusActiveEditor = useCallback(() => {
    (richEditorRef.current ?? textareaRef.current)?.focus();
  }, []);

  const getEffectiveSelectionRange = useCallback((): { start: number; end: number } => {
    if (!hasInlineReferences && textareaRef.current && document.activeElement === textareaRef.current) {
      return {
        start: textareaRef.current.selectionStart,
        end: textareaRef.current.selectionEnd,
      };
    }
    if (userSelectedRef.current) {
      const clampedStart = Math.max(0, Math.min(activeCanonicalInput.length, selectionRangeRef.current.start));
      const clampedEnd = Math.max(clampedStart, Math.min(activeCanonicalInput.length, selectionRangeRef.current.end));
      return { start: clampedStart, end: clampedEnd };
    }
    return { start: activeCanonicalInput.length, end: activeCanonicalInput.length };
  }, [activeCanonicalInput.length, hasInlineReferences]);

  const pickerList = useCallback((id: string, active: string | undefined) => setList((previous) => previous?.id === id && previous.active === active ? previous : { id, active }), []);
  const closePicker = useCallback(() => { setPicker(undefined); focusActiveEditor(); }, [focusActiveEditor]);

  useEffect(() => {
    setPicker(undefined);
    setShowStatus(false);
    setAgentCreator(undefined);
    setPendingCaretOffset(null);
    selectionRangeRef.current = { start: 0, end: 0 };
    userSelectedRef.current = false;
  }, [context?.sessionId]);

  useEffect(() => context?.registerEditor?.({
    getSelectionRange: () => getEffectiveSelectionRange(),
    onInsertedCaret: (offset) => {
      userSelectedRef.current = true;
      selectionRangeRef.current = { start: offset, end: offset };
      setPendingCaretOffset(offset);
    },
  }), [context, getEffectiveSelectionRange]);

  function updateMention(value: string, position: number) {
    const prefix = value.slice(0, position);
    const slashMatch = /(?:^|\s|\(ref:[A-Za-z0-9_-]{1,64}\))(\/)([^\s[\]]*)$/.exec(prefix);
    const match =
      slashMatch
      ?? /(?:^|\s|\(ref:[A-Za-z0-9_-]{1,64}\)|[\u3000-\u303F\u4E00-\u9FFF\uFF00-\uFFEF])([@$])([^\s[\]]*)$/.exec(prefix);
    if (!match) { setPicker(undefined); return; }
    const symbol = match[1]!;
    const query = match[2]!;
    const start = position - query.length - 1;
    setPicker({
      mode: symbol === "$" ? "skill" : symbol === "/" ? "commands" : "all",
      query,
      start,
      end: position,
      insertRange: { start, end: start },
    });
  }

  function consumeMention(): { start: number; end: number } | undefined {
    if (picker?.start !== undefined && picker.end !== undefined) {
      const position = picker.start;
      const stripped = activeCanonicalInput.slice(0, position) + activeCanonicalInput.slice(picker.end);
      userSelectedRef.current = true;
      selectionRangeRef.current = { start: position, end: position };
      onInputChange(stripped);
      setPendingCaretOffset(position);
      setPicker(undefined);
      return { start: position, end: position };
    }
    const fallbackRange = picker?.insertRange ?? getEffectiveSelectionRange();
    closePicker();
    return fallbackRange;
  }

  useImperativeHandle(forwardedRef, () => (richEditorRef.current ?? textareaRef.current) as HTMLTextAreaElement);

  const [showBusyHint, setShowBusyHint] = useState(false);
  useEffect(() => {
    if (!context?.busy) {
      setShowBusyHint(false);
      return;
    }
    const timer = setTimeout(() => {
      setShowBusyHint(true);
    }, 250);
    return () => clearTimeout(timer);
  }, [context?.busy]);

  const isReadOnly = composerMode === "read_only";
  const isIdle = composerMode === "idle";
  const canCancel = (composerMode === "running" || composerMode === "starting")
    && Boolean(activeRun?.allowedActions?.includes("cancel"));
  const hasDraft = Boolean(input.trim()) || references.length > 0;
  const showCancelButton = canCancel && !hasDraft;
  const inputDisabled = !draftReady || isSubmitting;

  const prevDisabledRef = useRef<boolean>(inputDisabled);

  useEffect(() => {
    let frameHandle: number | undefined;
    // Focus active editor when transition from disabled -> enabled occurs
    if (prevDisabledRef.current && !inputDisabled) {
      frameHandle = requestAnimationFrame(() => {
        focusActiveEditor();
      });
    }
    prevDisabledRef.current = inputDisabled;

    return () => {
      if (frameHandle !== undefined) {
        cancelAnimationFrame(frameHandle);
      }
    };
  }, [inputDisabled, focusActiveEditor]);

  const prevHasInlineRef = useRef<boolean>(hasInlineReferences);

  useLayoutEffect(() => {
    if (prevHasInlineRef.current && !hasInlineReferences && !inputDisabled) {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        const pos = Math.max(0, Math.min(el.value.length, selectionRangeRef.current.start));
        el.setSelectionRange(pos, pos);
      }
    }
    prevHasInlineRef.current = hasInlineReferences;
  }, [hasInlineReferences, inputDisabled]);

  useLayoutEffect(() => {
    if (!hasInlineReferences && pendingCaretOffset !== null && !inputDisabled) {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        const pos = Math.max(0, Math.min(el.value.length, pendingCaretOffset));
        el.setSelectionRange(pos, pos);
        selectionRangeRef.current = { start: pos, end: pos };
      }
      setPendingCaretOffset(null);
    }
  }, [hasInlineReferences, inputDisabled, pendingCaretOffset, input]);

  const handlePasteFiles = useCallback((files: File[]): boolean => {
    if (!files.length) return false;
    const paths = files.map((file) => window.eidosRuntime.inputPathForFile(file)).filter(Boolean);
    if (paths.length) {
      void context?.paths(paths);
      return true;
    }
    if (files.some((file) => file.type.startsWith("image/"))) {
      void context?.paste();
      return true;
    }
    return false;
  }, [context]);

  const placeholder = modelLoading
    ? "正在加载模型配置…"
    : isReadOnly
      ? "暂无法执行，可继续编辑草稿"
      : modelConfigured
        ? workMode === "plan"
          ? "先把想法变成计划"
          : "想做什么，从这里开始"
        : "请先在设置中添加模型";

  const statusLabel = modelLoading
    ? "正在加载模型…"
    : composerMode === "running" || composerMode === "starting"
        ? statusText(activeRun?.status ?? "queued")
        : composerMode === "waiting_approval"
            ? "等待批准"
            : composerMode === "finalizing"
              ? "正在收尾"
              : selectedModelId ?? "无可用模型";

  const buttonLabel = modelLoading
    ? "加载中…"
    : submitKind === "start" || composerMode === "starting"
        ? "启动中…"
        : activeRun ? "排队" : "开始";

  const unsupportedImage = references.some((reference) => reference.kind === "image") && !modelList?.models.find((model) => model.id === selectedModelId)?.supportsImages;

  const isSubmitDisabled =
    !draftReady
    || unsupportedImage
    || Boolean(context?.busy)
    || modelLoading
    || isSubmitting
    || composerMode === "starting"
    || composerMode === "read_only"
    || !modelConfigured
    || !selectedModelId
    || (!input.trim() && references.length === 0);

  const hasProjectContext = showSessionContext && (project !== undefined || projectless);
  const selectedModel = modelList?.models.find((model) => model.id === selectedModelId);
  const modelReasoning = selectedModel?.reasoning ?? undefined;
  const selectedReasoning = reasoningSelection ?? modelReasoning?.defaultSelection ?? undefined;
  const projectName = project?.name?.trim() || (project ? basename(project.workspaceRoot) : undefined);

  return (
    <form
      className="composer"
      ref={composerRef}
      onSubmit={(e) => { e.preventDefault(); if (!isSubmitDisabled) onSubmit(); }}
    >
      {hasProjectContext && (
        <div className="composer-context" aria-label="会话上下文">
          {project ? (
            <>
              <span className="composer-context-project" title={project.workspaceRoot}>
                {onLeaveProject ? (
                  <button
                    type="button"
                    className="composer-context-project-action"
                    aria-label="不在项目中工作"
                    title="不在项目中工作"
                    disabled={isSubmitting}
                    onClick={() => onLeaveProject()}
                  >
                    <FolderIcon />
                    <span className="composer-context-project-close" aria-hidden="true">×</span>
                  </button>
                ) : (
                  <FolderIcon />
                )}
                <span>{projectName}</span>
              </span>
              {project.gitAvailable && (
                <>
                  <label className="composer-context-mode">
                    <span className="sr-only">执行方式</span>
                    <select
                      aria-label="执行方式"
                      value={executionMode ?? "local"}
                      disabled={isSubmitting || composerMode !== "idle" || !onExecutionModeChange}
                      onChange={(event) => onExecutionModeChange?.(event.target.value as "local" | "worktree")}
                    >
                      <option value="local">本地</option>
                      <option value="worktree">新建本地工作树</option>
                    </select>
                  </label>
                  {onBranchChange && branches && branches.length > 0 ? (
                    <label className="composer-context-branch">
                      <BranchIcon />
                      <span className="sr-only">本地分支</span>
                      <select
                        aria-label="本地分支"
                        value={branch ?? ""}
                        disabled={isSubmitting || Boolean(activeRun) || branchChanging || !isIdle}
                        onChange={(event) => onBranchChange(event.target.value)}
                      >
                        {branch === null && <option value="">Detached HEAD</option>}
                        {branches.map((name) => <option key={name} value={name}>{name}</option>)}
                      </select>
                    </label>
                  ) : (
                    <span className="composer-context-branch" title={branch ?? undefined}>
                      <BranchIcon />
                      <span>{branch ?? "未选择分支"}</span>
                    </span>
                  )}
                </>
              )}
            </>
          ) : (
            onSelectProject ? (
              <button
                type="button"
                className="composer-context-project composer-context-project--empty"
                onClick={() => onSelectProject()}
                disabled={isSubmitting}
              >
                <FolderIcon />
                <span>选择项目</span>
              </button>
            ) : (
              <span className="composer-context-project composer-context-project--empty">
                <FolderIcon />
                <span>无项目</span>
              </span>
            )
          )}
        </div>
      )}
      {shelfReferences.length > 0 && (
        <InputReferenceCards
          references={shelfReferences}
          variant="composer"
          placement="shelf"
          {...(onRemoveReference ? { onRemove: onRemoveReference } : {})}
        />
      )}
      {unsupportedImage && (
        <ToastContainer
          toasts={[{
            id: "composer-unsupported-image",
            message: "当前模型不支持图片。请选择支持图片的模型，或移除图片引用。",
            type: "warning",
            duration: 4000,
          }]}
        />
      )}
      {showBusyHint && <p className="input-reference-hint" role="status">正在准备引用…</p>}
      {(context?.error || inlineActivationError) && (
        <ToastContainer
          toasts={[{
            id: "composer-reference-error",
            message: (context?.error ?? inlineActivationError)!,
            type: "error",
            duration: 3500,
          }]}
        />
      )}
      {showStatus && <p className="input-reference-hint">{context?.sessionId} · {contextUsage ? formatContextUsage(contextUsage) : "暂无上下文统计"}</p>}
      {picker && <InputPicker mode={picker.mode} query={picker.query} inline={picker.start !== undefined}
        anchor={composerRef}
        onCreateAgent={context ? () => {
          const insertRange = picker.insertRange ?? getEffectiveSelectionRange();
          setAgentCreator({ owner: context.sessionId, add: context.add, insertRange });
          closePicker();
        } : undefined}
        onQuery={(query) => setPicker((previous) => previous ? { ...previous, query } : previous)}
        onListId={pickerList} onClose={closePicker}
        onChoose={(request) => {
          const insertRange = consumeMention();
          void context?.add(request, insertRange);
        }}
        onCommand={(command) => {
          consumeMention();
          if (command === "plan") onWorkModeChange?.("plan");
          else if (command === "status") setShowStatus((value) => !value);
          else if (command === "model") {
            composerRef.current?.querySelector<HTMLButtonElement>(".model-selector > button")?.click();
          } else context?.settings(command);
        }}
        onSelectPlanMode={() => onWorkModeChange?.("plan")} />}
      {agentCreator && <AgentEditorDialog referenceAfterSave onClose={() => { setAgentCreator(undefined); focusActiveEditor(); }}
        onSaved={(saved) => {
          void agentCreator.add({ kind: "agent", source: saved.definition.name }, agentCreator.insertRange);
          setAgentCreator(undefined); focusActiveEditor();
        }} />}
      <label id="task-input-label" className="sr-only" htmlFor="task-input">告诉 Eidos 要做什么</label>
      {hasInlineReferences ? (
        <>
          <InlineRichEditor
            ref={richEditorRef}
            id="task-input"
            value={input}
            inlineReferences={inlineReferences}
            placeholder={placeholder}
            disabled={inputDisabled}
            maxLength={65536}
            role={picker ? "combobox" : "textbox"}
            ariaLabelledBy="task-input-label"
            ariaExpanded={Boolean(picker)}
            ariaControls={picker ? list?.id : undefined}
            ariaActiveDescendant={picker ? list?.active : undefined}
            pendingCaretOffset={pendingCaretOffset}
            onPendingCaretConsumed={() => setPendingCaretOffset(null)}
            onSelectionChange={(range) => {
              userSelectedRef.current = true;
              selectionRangeRef.current = range;
            }}
            onChange={(nextValue, caretOffset) => {
              userSelectedRef.current = true;
              selectionRangeRef.current = { start: caretOffset, end: caretOffset };
              onInputChange(nextValue);
              updateMention(nextValue, caretOffset);
            }}
            onRemoveReference={onRemoveReference}
            onActivateReference={(reference) => void activateInlineReference(reference)}
            onSubmit={() => {
              if (!isSubmitDisabled) onSubmit();
            }}
            onPasteFiles={handlePasteFiles}
          />
          {inlineActivationDialogs}
        </>
      ) : (
        <textarea
          ref={textareaRef}
          id="task-input"
          rows={1}
          maxLength={65536}
          role={picker ? "combobox" : undefined}
          aria-autocomplete="list"
          aria-expanded={Boolean(picker)}
          aria-controls={picker ? list?.id : undefined}
          aria-activedescendant={picker ? list?.active : undefined}
          placeholder={placeholder}
          value={input}
          disabled={inputDisabled}
          onSelect={(event) => {
            userSelectedRef.current = true;
            selectionRangeRef.current = {
              start: event.currentTarget.selectionStart,
              end: event.currentTarget.selectionEnd,
            };
          }}
          onChange={(e) => {
            userSelectedRef.current = true;
            selectionRangeRef.current = {
              start: e.target.selectionStart,
              end: e.target.selectionEnd,
            };
            onInputChange(e.target.value);
            if (!(e.nativeEvent as InputEvent).isComposing) updateMention(e.target.value, e.target.selectionStart);
          }}
          onCompositionEnd={(event) => {
            userSelectedRef.current = true;
            selectionRangeRef.current = {
              start: event.currentTarget.selectionStart,
              end: event.currentTarget.selectionEnd,
            };
            updateMention(event.currentTarget.value, event.currentTarget.selectionStart);
          }}
          onClick={(event) => {
            userSelectedRef.current = true;
            selectionRangeRef.current = {
              start: event.currentTarget.selectionStart,
              end: event.currentTarget.selectionEnd,
            };
            updateMention(event.currentTarget.value, event.currentTarget.selectionStart);
          }}
          onKeyUp={(event) => {
            userSelectedRef.current = true;
            selectionRangeRef.current = {
              start: event.currentTarget.selectionStart,
              end: event.currentTarget.selectionEnd,
            };
          }}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData.files);
            if (handlePasteFiles(files)) {
              event.preventDefault();
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (!isSubmitDisabled) onSubmit();
            }
          }}
        />
      )}
      <div className="composer-actions">
        <div className="composer-meta">
          <Tooltip content="添加文件、扩展或历史对话">
            <button type="button" className="composer-add" aria-label="添加引用" aria-expanded={Boolean(picker)}
              disabled={!draftReady || isSubmitting || !context}
              onClick={() => {
                const insertRange = getEffectiveSelectionRange();
                setPicker((previous) => previous ? undefined : { mode: "all", query: "", insertRange });
              }}>＋</button>
          </Tooltip>
          {onApprovalModeChange && (
            <ApprovalModeSelector
              mode={approvalMode}
              disabled={isSubmitting}
              onChange={onApprovalModeChange}
            />
          )}
          {workMode === "plan" && (
            <Tooltip content="退出 Plan 模式">
              <button
                type="button"
                className="composer-plan-badge"
                disabled={isSubmitting}
                onClick={() => onWorkModeChange?.("execute")}
                aria-label="退出 Plan 模式"
              >
                <span className="composer-plan-badge__icon" aria-hidden="true">
                  <LightbulbIcon className="composer-plan-badge__bulb" />
                  <CloseIcon className="composer-plan-badge__close" />
                </span>
                <span className="composer-plan-badge__text">Plan 模式</span>
              </button>
            </Tooltip>
          )}
          {!isIdle && <span>{statusLabel}</span>}
          {!modelConfigured && (
            <Button type="button" variant="ghost" size="small" onClick={onOpenModelSettings}>
              前往模型设置
            </Button>
          )}
        </div>

        <div className="composer-options">
          {showContextIndicator && <ContextIndicator key={contextRunId} usage={contextUsage} details={contextDetails} runId={contextRunId} />}
          {modelList && modelList.models.length > 0 && (
            <ModelSelector
              models={modelList.models}
              selectedModelId={selectedModelId}
              selection={selectedReasoning}
              disabled={modelLoading || isSubmitting}
              onModelChange={onModelChange}
              onChange={(selection) => onReasoningSelectionChange?.(selection)}
            />
          )}

          {showCancelButton ? (
            <Tooltip content={cancelingRunId ? "取消中…" : "取消 Run"}>
              <Button
                type="button"
                variant="primary"
                size="medium"
                className="composer-submit-btn composer-cancel-btn"
                disabled={Boolean(cancelingRunId)}
                loading={Boolean(cancelingRunId)}
                onClick={onCancel}
                aria-label={cancelingRunId ? "取消中…" : "取消 Run"}
                icon={!cancelingRunId ? <StopSquareIcon /> : undefined}
              >
                <span className="sr-only">{cancelingRunId ? "取消中…" : "取消 Run"}</span>
              </Button>
            </Tooltip>
          ) : (
            <Tooltip content={buttonLabel}>
              <Button
                type="submit"
                variant="primary"
                size="medium"
                className={`composer-submit-btn${!hasDraft ? " composer-submit-btn--empty" : ""}`}
                disabled={isSubmitDisabled}
                loading={isSubmitting || composerMode === "starting"}
                aria-label={buttonLabel}
                icon={
                  !isSubmitting && composerMode !== "starting" ? (
                    <UpArrowIcon />
                  ) : undefined
                }
              >
                <span className="sr-only">{buttonLabel}</span>
              </Button>
            </Tooltip>
          )}
        </div>
      </div>
    </form>
  );
});

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" aria-hidden="true">
      <path d="M2.5 5C2.5 3.9 3.4 3 4.5 3h3.1c.5 0 1 .2 1.3.6L10 5h5.5C16.9 5 18 6.1 18 7.5v7C18 15.9 16.9 17 15.5 17h-11C3.1 17 2 15.9 2 14.5v-9c0-.3.2-.5.5-.5Z" fill="currentColor" fillOpacity=".12" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

function BranchIcon() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" aria-hidden="true">
      <circle cx="6" cy="4" r="2" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="6" cy="16" r="2" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="15" cy="5" r="2" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6 6v6M8 14c3.7 0 7-1.6 7-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function UpArrowIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <path
        d="M8 13.5V2.5M3.5 7L8 2.5L12.5 7"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function StopSquareIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <rect x="3" y="3" width="10" height="10" rx="1.8" fill="currentColor" />
    </svg>
  );
}

export function CloseIcon({ className }: { className?: string } = {}) {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true" className={className}>
      <path d="m4 4 8 8m0-8-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function statusText(status: Run["status"]): string {
  return ({
    queued: "已排队", running: "正在执行", waiting_input: "等待回答", waiting_agents: "等待子任务", waiting_approval: "等待批准",
    finalizing: "正在收尾", stopped: "已停止",
    succeeded: "已完成", failed: "失败", canceled: "已取消", interrupted: "已中断",
  } as const)[status];
}
