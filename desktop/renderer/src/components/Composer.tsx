import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef } from "react";
import type { ModelId, Run } from "../contracts.js";
import type { ComposerMode } from "../session-state.js";
import { Button } from "./Button.js";

export interface ComposerProps {
  composerMode: ComposerMode;
  activeRun: Run | undefined;
  input: string;
  modelList: import("../contracts.js").ModelListResult | undefined;
  selectedModelId: ModelId | undefined;
  modelConfigured: boolean;
  modelLoading: boolean;
  isSubmitting: boolean;
  submitKind: "start" | undefined;
  hasRuns: boolean;
  cancelingRunId: string | undefined;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onModelChange: (id: ModelId) => void;
}

export const Composer = forwardRef<HTMLTextAreaElement, ComposerProps>(function Composer({
  composerMode,
  activeRun,
  input,
  modelList,
  selectedModelId,
  modelConfigured,
  modelLoading,
  isSubmitting,
  submitKind,
  hasRuns,
  cancelingRunId,
  onInputChange,
  onSubmit,
  onCancel,
  onModelChange,
}, forwardedRef) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(forwardedRef, () => textareaRef.current as HTMLTextAreaElement);

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const minHeight = 56;
    const maxHeight = 168;
    const targetHeight = Math.min(Math.max(el.scrollHeight, minHeight), maxHeight);
    el.style.height = `${targetHeight}px`;
  }, [input]);

  const isReadOnly = composerMode === "read_only";
  const isIdle = composerMode === "idle";
  const canCancel = (composerMode === "running" || composerMode === "starting") && activeRun?.allowedActions?.includes("cancel");
  const inputDisabled = modelLoading || isSubmitting || !modelConfigured || !selectedModelId || isReadOnly || composerMode === "finalizing" || composerMode === "waiting_approval";

  const prevDisabledRef = useRef<boolean>(inputDisabled);

  useEffect(() => {
    let frameHandle: number | undefined;
    // Focus textarea when transition from disabled -> enabled occurs
    if (prevDisabledRef.current && !inputDisabled) {
      frameHandle = requestAnimationFrame(() => {
        textareaRef.current?.focus();
      });
    }
    prevDisabledRef.current = inputDisabled;

    return () => {
      if (frameHandle !== undefined) {
        cancelAnimationFrame(frameHandle);
      }
    };
  }, [inputDisabled]);

  const placeholder = modelLoading
    ? "正在加载模型配置…"
    : isReadOnly
      ? "存储只读，暂无法启动 Run"
      : modelConfigured
        ? "例如：阅读这个项目并说明如何启动"
        : "请先配置 DeepSeek API Key";

  const showModelSelect = isIdle && !hasRuns;
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
        : "开始";

  const isSubmitDisabled =
    modelLoading
    || isSubmitting
    || composerMode === "starting"
    || composerMode === "running"
    || composerMode === "finalizing"
    || composerMode === "waiting_approval"
    || composerMode === "read_only"
    || !modelConfigured
    || !selectedModelId
    || !input.trim();

  return (
    <form
      className="composer"
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
    >
      <label className="sr-only" htmlFor="task-input">告诉 Eidos 要做什么</label>
      <textarea
        ref={textareaRef}
        id="task-input"
        rows={2}
        placeholder={placeholder}
        value={input}
        disabled={inputDisabled}
        onChange={(e) => onInputChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
      <div className="composer-actions">
        <div className="composer-meta">
          {showModelSelect ? (
            <>
              <label htmlFor="run-model">本次模型</label>
              <select
                id="run-model"
                value={selectedModelId ?? ""}
                disabled={composerMode !== "idle" || modelLoading || isSubmitting}
                onChange={(e) => onModelChange(e.target.value as ModelId)}
              >
                {modelList?.models.map((option) => (
                  <option key={option.id} value={option.id} disabled={!option.selectable}>
                    {option.displayName}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <span>{statusLabel}</span>
          )}
        </div>

        {canCancel ? (
          <Button
            type="button"
            variant="primary"
            size="medium"
            className="composer-submit-btn composer-cancel-btn"
            disabled={Boolean(cancelingRunId)}
            loading={Boolean(cancelingRunId)}
            onClick={onCancel}
            aria-label={cancelingRunId ? "取消中…" : "取消 Run"}
            title={cancelingRunId ? "取消中…" : "取消 Run"}
            icon={!cancelingRunId ? <StopSquareIcon /> : undefined}
          >
            <span className="sr-only">{cancelingRunId ? "取消中…" : "取消 Run"}</span>
          </Button>
        ) : (
          <Button
            type="submit"
            variant="primary"
            size="medium"
            className={`composer-submit-btn${!input.trim() ? " composer-submit-btn--empty" : ""}`}
            disabled={isSubmitDisabled}
            loading={isSubmitting || composerMode === "starting"}
            aria-label={buttonLabel}
            title={buttonLabel}
            icon={
              !isSubmitting && composerMode !== "starting" ? (
                <UpArrowIcon />
              ) : undefined
            }
          >
            <span className="sr-only">{buttonLabel}</span>
          </Button>
        )}
      </div>
    </form>
  );
});

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

export function statusText(status: Run["status"]): string {
  return ({
    queued: "已排队", running: "正在执行", waiting_approval: "等待批准",
    finalizing: "正在收尾", stopped: "已停止",
    succeeded: "已完成", failed: "失败", canceled: "已取消", interrupted: "已中断",
  } as const)[status];
}
