import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button.js";
import { FieldError } from "./FieldError.js";
import { MarkdownContent } from "./MarkdownContent.js";
import { Tooltip } from "./Tooltip.js";

export interface MarkdownEditorProps {
  /** 标题，显示在编辑器左上方（例如 "Agent.md" 或 "Markdown 正文"） */
  title?: string | undefined;
  /** 标题是否使用等宽代码字体展示 */
  titleCodeStyle?: boolean | undefined;
  /** 是否必填字段（显示红色星号） */
  required?: boolean | undefined;
  /** 是否隐藏标题 */
  hideTitle?: boolean | undefined;
  /** 是否显示字数/字符数计数徽标（默认 true） */
  showCount?: boolean | undefined;
  /** 是否隐藏字数/字符数计数徽标 */
  hideCount?: boolean | undefined;
  /** Markdown 文本内容 */
  value: string;
  /** 内容变更回调 */
  onChange: (value: string) => void;
  onBlur?: (() => void) | undefined;
  /** 占位符文本 */
  placeholder?: string | undefined;
  /** 无障碍标签（默认使用 title 或 "Markdown 正文"） */
  ariaLabel?: string | undefined;
  /** 最大字符数 */
  maxLength?: number | undefined;
  /** 默认文本行数（未开启 fillParent 时有效） */
  rows?: number | undefined;
  /** 是否禁用编辑 */
  disabled?: boolean | undefined;
  /** 字段校验错误信息 */
  error?: string | null | undefined;
  /** 是否撑满父级容器高度并锁定内部滚动 */
  fillParent?: boolean | undefined;
  /** 是否显示全屏放大编辑按钮（默认 true） */
  allowExpand?: boolean | undefined;
  /** 受控查看模式（"edit" 为源码，"preview" 为渲染预览） */
  viewMode?: "edit" | "preview" | undefined;
  /** 查看模式变更回调 */
  onViewModeChange?: ((mode: "edit" | "preview") => void) | undefined;
  /** 放大弹窗状态变更回调 */
  onExpandChange?: ((expanded: boolean) => void) | undefined;
  /** 外部传入的文本框 ref */
  textareaRef?: React.RefObject<HTMLTextAreaElement | null> | undefined;
  /** 额外的容器类名 */
  className?: string | undefined;
  /** 预览为空时的提示语 */
  emptyPreviewText?: string | undefined;
}

export function MarkdownEditor({
  title,
  titleCodeStyle,
  required,
  hideTitle = false,
  showCount = true,
  hideCount = false,
  value,
  onChange,
  onBlur,
  placeholder,
  ariaLabel,
  maxLength = 65536,
  rows = 10,
  disabled = false,
  error,
  fillParent = false,
  allowExpand = true,
  viewMode: controlledViewMode,
  onViewModeChange,
  onExpandChange,
  textareaRef,
  className,
  emptyPreviewText = "暂无内容，请切换至「源码」编写 Markdown 格式的正文。",
}: MarkdownEditorProps) {
  const [internalViewMode, setInternalViewMode] = useState<"edit" | "preview">("edit");
  const [expanded, setExpanded] = useState(false);
  const localTextareaRef = useRef<HTMLTextAreaElement | null>(null);

  const viewMode = controlledViewMode ?? internalViewMode;

  function handleViewModeChange(nextMode: "edit" | "preview") {
    onViewModeChange?.(nextMode);
    setInternalViewMode(nextMode);
  }

  function handleOpenExpand() {
    setExpanded(true);
    onExpandChange?.(true);
  }

  function handleCloseExpand() {
    setExpanded(false);
    onExpandChange?.(false);
    setTimeout(() => {
      localTextareaRef.current?.focus();
    }, 0);
  }

  function setMergedRef(node: HTMLTextAreaElement | null) {
    localTextareaRef.current = node;
    if (textareaRef) {
      if (typeof textareaRef === "function") {
        (textareaRef as (instance: HTMLTextAreaElement | null) => void)(node);
      } else {
        (textareaRef as React.MutableRefObject<HTMLTextAreaElement | null>).current = node;
      }
    }
  }

  const effectiveAriaLabel = ariaLabel ?? title ?? "Markdown 正文";
  const shouldShowTitle = Boolean(title) && !hideTitle;
  const shouldShowCount = showCount && !hideCount;
  const hasTitleGroup = shouldShowTitle || shouldShowCount;

  return (
    <div
      className={`markdown-editor ${fillParent ? "markdown-editor--fill" : ""} ${className ?? ""}`}
    >
      <div className={`markdown-editor__header agent-editor__section-header-row ${!hasTitleGroup ? "markdown-editor__header--toolbar-only" : ""}`}>
        {hasTitleGroup ? (
          <div className="markdown-editor__title-group agent-editor__title-group">
            {shouldShowTitle ? (
              <span
                className={`markdown-editor__label-title ${
                  titleCodeStyle ? "markdown-editor__label-title--code agent-editor__label-title--code" : ""
                }`}
              >
                {title}
                {required && (
                  <span className="field-required" aria-hidden="true">
                    *
                  </span>
                )}
              </span>
            ) : null}
            {shouldShowCount ? (
              <span className="markdown-editor__bytes-badge agent-editor__bytes-badge" title="字符数">
                {value.length}
              </span>
            ) : null}
          </div>
        ) : null}

        <div className="markdown-editor__toolbar agent-editor__instructions-toolbar">
          <div
            className="markdown-editor__segmented-bar agent-editor__segmented-bar--sm"
            role="tablist"
            aria-label="查看方式"
          >
            <Tooltip content="查看与编辑源码">
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === "edit"}
                className={`markdown-editor__toolbar-tab agent-editor__toolbar-tab ${
                  viewMode === "edit" ? "markdown-editor__toolbar-tab--active agent-editor__toolbar-tab--active" : ""
                }`}
                onClick={() => handleViewModeChange("edit")}
                title="查看与编辑源码"
              >
                <CodeIcon className="markdown-editor__tab-icon agent-editor__tab-icon" />
                <span>源码</span>
              </button>
            </Tooltip>
            <Tooltip content="预览 Markdown 效果">
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === "preview"}
                className={`markdown-editor__toolbar-tab agent-editor__toolbar-tab ${
                  viewMode === "preview" ? "markdown-editor__toolbar-tab--active agent-editor__toolbar-tab--active" : ""
                }`}
                onClick={() => handleViewModeChange("preview")}
                title="预览 Markdown 效果"
              >
                <PreviewIcon className="markdown-editor__tab-icon agent-editor__tab-icon" />
                <span>预览</span>
              </button>
            </Tooltip>
          </div>

          {allowExpand && (
            <Tooltip content="放大编辑">
              <button
                type="button"
                className="markdown-editor__icon-btn agent-editor__icon-btn"
                onClick={handleOpenExpand}
                title="放大编辑"
                aria-label="放大编辑"
              >
                <ExpandIcon />
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      <div className="markdown-editor__body">
        {viewMode === "edit" ? (
          <textarea
            ref={setMergedRef}
            aria-label={effectiveAriaLabel}
            required={required}
            maxLength={maxLength}
            rows={rows}
            value={value}
            placeholder={placeholder}
            disabled={disabled}
            className="markdown-editor__textarea agent-editor__textarea"
            onChange={(event) => onChange(event.target.value)}
            onBlur={onBlur}
          />
        ) : (
          <div className="markdown-editor__preview-wrapper agent-editor__preview-wrapper">
            {value.trim() ? (
              <div
                className="markdown-editor__preview-body agent-editor__preview-body eidos-chat-prose"
                tabIndex={0}
                role="region"
                aria-label={`${effectiveAriaLabel} 预览`}
              >
                <MarkdownContent content={value} />
              </div>
            ) : (
              <div className="markdown-editor__preview-empty agent-editor__preview-empty">
                <p>{emptyPreviewText}</p>
              </div>
            )}
          </div>
        )}
      </div>

      {error ? <FieldError error={error} inline /> : null}

      {expanded &&
        createPortal(
          <MarkdownEditorExpandedDialog
            title={title}
            titleCodeStyle={titleCodeStyle}
            hideTitle={hideTitle}
            showCount={showCount}
            hideCount={hideCount}
            ariaLabel={effectiveAriaLabel}
            value={value}
            maxLength={maxLength}
            placeholder={placeholder}
            disabled={disabled}
            emptyPreviewText={emptyPreviewText}
            viewMode={viewMode}
            onChange={onChange}
            onBlur={onBlur}
            onChangeViewMode={handleViewModeChange}
            onClose={handleCloseExpand}
          />,
          document.body,
        )}
    </div>
  );
}

function MarkdownEditorExpandedDialog({
  title,
  titleCodeStyle,
  hideTitle,
  showCount,
  hideCount,
  ariaLabel,
  value,
  maxLength,
  placeholder,
  disabled,
  emptyPreviewText,
  viewMode,
  onChange,
  onBlur,
  onChangeViewMode,
  onClose,
}: {
  title?: string | undefined;
  titleCodeStyle?: boolean | undefined;
  hideTitle?: boolean | undefined;
  showCount?: boolean | undefined;
  hideCount?: boolean | undefined;
  ariaLabel: string;
  value: string;
  maxLength?: number | undefined;
  placeholder?: string | undefined;
  disabled?: boolean | undefined;
  emptyPreviewText: string;
  viewMode: "edit" | "preview";
  onChange: (value: string) => void;
  onBlur?: (() => void) | undefined;
  onChangeViewMode: (mode: "edit" | "preview") => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const shouldShowTitle = Boolean(title) && !hideTitle;
  const shouldShowCount = showCount !== false && !hideCount;
  const hasTitleGroup = shouldShowTitle || shouldShowCount;

  useEffect(() => {
    if (viewMode === "edit") {
      textareaRef.current?.focus();
    }
  }, [viewMode]);

  return (
    <div
      className="markdown-editor-expanded-backdrop agent-md-expanded-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={shouldShowTitle ? titleId : undefined}
        aria-label={!shouldShowTitle ? ariaLabel : undefined}
        className="markdown-editor-expanded-dialog agent-md-expanded-dialog"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
          }
        }}
      >
        <header className={`markdown-editor-expanded-header agent-md-expanded-header ${!hasTitleGroup ? "markdown-editor__header--toolbar-only" : ""}`}>
          {hasTitleGroup ? (
            <div className="markdown-editor__title-group agent-editor__title-group">
              {shouldShowTitle ? (
                <span
                  id={titleId}
                  className={`markdown-editor__label-title ${
                    titleCodeStyle ? "markdown-editor__label-title--code agent-editor__label-title--code" : ""
                  }`}
                >
                  {title ?? "Markdown 编辑"}
                </span>
              ) : null}
              {shouldShowCount ? (
                <span className="markdown-editor__bytes-badge agent-editor__bytes-badge" title="字符数">
                  {value.length}
                </span>
              ) : null}
            </div>
          ) : null}

          <div className="markdown-editor__toolbar agent-editor__instructions-toolbar">
            <div
              className="markdown-editor__segmented-bar agent-editor__segmented-bar--sm"
              role="tablist"
              aria-label="查看方式"
            >
              <Tooltip content="查看与编辑源码">
                <button
                  type="button"
                  role="tab"
                  aria-selected={viewMode === "edit"}
                  className={`markdown-editor__toolbar-tab agent-editor__toolbar-tab ${
                    viewMode === "edit" ? "markdown-editor__toolbar-tab--active agent-editor__toolbar-tab--active" : ""
                  }`}
                  onClick={() => onChangeViewMode("edit")}
                  title="查看与编辑源码"
                >
                  <CodeIcon className="markdown-editor__tab-icon agent-editor__tab-icon" />
                  <span>源码</span>
                </button>
              </Tooltip>
              <Tooltip content="预览 Markdown 效果">
                <button
                  type="button"
                  role="tab"
                  aria-selected={viewMode === "preview"}
                  className={`markdown-editor__toolbar-tab agent-editor__toolbar-tab ${
                    viewMode === "preview" ? "markdown-editor__toolbar-tab--active agent-editor__toolbar-tab--active" : ""
                  }`}
                  onClick={() => onChangeViewMode("preview")}
                  title="预览 Markdown 效果"
                >
                  <PreviewIcon className="markdown-editor__tab-icon agent-editor__tab-icon" />
                  <span>预览</span>
                </button>
              </Tooltip>
            </div>

            <Tooltip content="收起">
              <button
                type="button"
                className="markdown-editor__icon-btn agent-editor__icon-btn"
                onClick={onClose}
                title="收起"
                aria-label="收起"
              >
                <CompressIcon />
              </button>
            </Tooltip>
          </div>
        </header>

        <div className="markdown-editor-expanded-body agent-md-expanded-body">
          {viewMode === "edit" ? (
            <textarea
              ref={textareaRef}
              aria-label={ariaLabel}
              maxLength={maxLength}
              value={value}
              placeholder={placeholder}
              disabled={disabled}
              className="markdown-editor-expanded-textarea agent-md-expanded-textarea"
              onChange={(event) => onChange(event.target.value)}
              onBlur={onBlur}
            />
          ) : (
            <div className="markdown-editor-expanded-preview-wrapper agent-md-expanded-preview-wrapper">
              {value.trim() ? (
                <div
                  className="markdown-editor-expanded-preview-body agent-md-expanded-preview-body eidos-chat-prose"
                  tabIndex={0}
                  role="region"
                  aria-label={`${ariaLabel} 预览`}
                >
                  <MarkdownContent content={value} />
                </div>
              ) : (
                <div className="markdown-editor__preview-empty agent-editor__preview-empty">
                  <p>{emptyPreviewText}</p>
                </div>
              )}
            </div>
          )}
        </div>

        <footer className="markdown-editor-expanded-footer agent-md-expanded-footer">
          <span className="markdown-editor-expanded-tip agent-md-expanded-tip">
            按 Esc 或点击完成返回
          </span>
          <Button variant="primary" size="small" onClick={onClose}>
            完成
          </Button>
        </footer>
      </div>
    </div>
  );
}

function CodeIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="5 4 1 8 5 12" />
      <polyline points="11 4 15 8 11 12" />
    </svg>
  );
}

function PreviewIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M1.5 8C3 5 5.2 3.5 8 3.5S13 5 14.5 8C13 11 10.8 12.5 8 12.5S3 11 1.5 8Z" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="10 2 14 2 14 6" />
      <polyline points="6 14 2 14 2 10" />
      <line x1="14" y1="2" x2="9.5" y2="6.5" />
      <line x1="2" y1="14" x2="6.5" y2="9.5" />
    </svg>
  );
}

function CompressIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="3 7 7 7 7 3" />
      <polyline points="13 9 9 9 9 13" />
      <line x1="1" y1="1" x2="7" y2="7" />
      <line x1="15" y1="15" x2="9" y2="9" />
    </svg>
  );
}
