import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef } from "react";
import type { ContextUsage, ModelId, Run, Session } from "../contracts.js";
import type { ComposerMode } from "../session-state.js";
import { formatContextUsage } from "../context-usage.js";
import { Button } from "./Button.js";
import { ContextIndicator } from "./ContextIndicator.js";

export interface ComposerProps {
  composerMode: ComposerMode;
  activeRun: Run | undefined;
  input: string;
  modelList: import("../contracts.js").ModelListResult | undefined;
  selectedModelId: ModelId | undefined;
  contextUsage: ContextUsage | undefined;
  modelConfigured: boolean;
  modelLoading: boolean;
  isSubmitting: boolean;
  submitKind: "start" | undefined;
  cancelingRunId: string | undefined;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onModelChange: (id: ModelId) => void;
  onOpenModelSettings: () => void;
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
  modelList,
  selectedModelId,
  contextUsage,
  modelConfigured,
  modelLoading,
  isSubmitting,
  submitKind,
  cancelingRunId,
  onInputChange,
  onSubmit,
  onCancel,
  onModelChange,
  onOpenModelSettings,
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

  const hasProjectContext = showSessionContext && (project !== undefined || projectless);
  const projectName = project?.name?.trim() || (project ? basename(project.workspaceRoot) : undefined);

  return (
    <form
      className="composer"
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
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
                    disabled={isSubmitting || Boolean(activeRun)}
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
                      <option value="local">本地工作区</option>
                      <option value="worktree">受管工作树</option>
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
                disabled={isSubmitting || Boolean(activeRun)}
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
          {!isIdle && <span>{statusLabel}</span>}
          {!modelConfigured && (
            <Button type="button" variant="ghost" size="small" onClick={onOpenModelSettings}>
              前往模型设置
            </Button>
          )}
        </div>

        <div className="composer-options">
          <ContextIndicator usage={contextUsage} />
          <label htmlFor="run-model" className="sr-only">
            本次模型
          </label>
          <select
            id="run-model"
            value={selectedModelId ?? ""}
            disabled={composerMode !== "idle" || modelLoading || isSubmitting || !modelConfigured}
            onChange={(e) => onModelChange(e.target.value as ModelId)}
          >
            {modelList?.models.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name}
              </option>
            ))}
          </select>

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

export function statusText(status: Run["status"]): string {
  return ({
    queued: "已排队", running: "正在执行", waiting_approval: "等待批准",
    finalizing: "正在收尾", stopped: "已停止",
    succeeded: "已完成", failed: "失败", canceled: "已取消", interrupted: "已中断",
  } as const)[status];
}
