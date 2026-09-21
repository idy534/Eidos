import { useEffect, useId, useRef, useState } from "react";
import type { ApprovalMode } from "../contracts.js";
import { ConfirmDialog } from "./settings/ConfirmDialog.js";

export interface ApprovalModeSelectorProps {
  mode: ApprovalMode;
  disabled?: boolean;
  onChange: (mode: ApprovalMode) => void;
}

interface ModeOption {
  id: ApprovalMode;
  name: string;
  badge?: string;
  description: string;
}

const MODE_OPTIONS: readonly ModeOption[] = [
  {
    id: "manual",
    name: "请求审批",
    description: "需要审批时由你批准或拒绝。",
  },
  {
    id: "auto_review",
    name: "替我审批",
    badge: "推荐",
    description: "模型处理原本需要审批的操作",
  },
  {
    id: "full_access",
    name: "完全访问",
    badge: "风险",
    description: "完全访问互联网和电脑上的所有文件。",
  },
];

const DEFAULT_OPTION: ModeOption = {
  id: "manual",
  name: "请求审批",
  description: "需要审批时由你批准或拒绝。",
};

export function ApprovalModeSelector({
  mode,
  disabled = false,
  onChange,
}: ApprovalModeSelectorProps) {
  const panelId = useId();
  const radioName = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [showRiskDialog, setShowRiskDialog] = useState(false);

  const currentOption = MODE_OPTIONS.find((opt) => opt.id === mode) ?? DEFAULT_OPTION;

  const handleSelectMode = (nextMode: ApprovalMode) => {
    if (nextMode === "full_access") {
      if (mode === "full_access") {
        setOpen(false);
        return;
      }
      setOpen(false);
      setShowRiskDialog(true);
      return;
    }
    onChange(nextMode);
    setOpen(false);
    triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return;

    const closeOnOutsideInteraction = (event: Event) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) {
        setOpen(false);
      }
    };

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };

    document.addEventListener("pointerdown", closeOnOutsideInteraction, true);
    document.addEventListener("focusin", closeOnOutsideInteraction, true);
    document.addEventListener("keydown", closeOnEscape, true);

    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideInteraction, true);
      document.removeEventListener("focusin", closeOnOutsideInteraction, true);
      document.removeEventListener("keydown", closeOnEscape, true);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="approval-mode-selector">
      <button
        ref={triggerRef}
        type="button"
        className={`approval-mode-selector__trigger${
          mode === "full_access" ? " approval-mode-selector__trigger--danger" : ""
        }`}
        aria-label={`审批模式：${currentOption.name}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        disabled={disabled}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className="approval-mode-selector__trigger-text">{currentOption.name}</span>
        <ChevronIcon />
      </button>

      {open && (
        <div
          id={panelId}
          className="approval-mode-selector__panel"
          role="dialog"
          aria-label="选择审批模式"
        >
          <header className="approval-mode-selector__header">
            <h2>审批模式</h2>
          </header>
          <fieldset className="approval-mode-selector__list" aria-label="审批模式选项">
            {MODE_OPTIONS.map((option) => (
              <label
                key={option.id}
                className={`approval-mode-selector__choice${
                  option.id === mode ? " is-selected" : ""
                }${option.id === "full_access" ? " approval-mode-selector__choice--full-access" : ""}`}
              >
                <input
                  type="radio"
                  name={radioName}
                  value={option.id}
                  checked={option.id === mode}
                  disabled={disabled}
                  onChange={() => handleSelectMode(option.id)}
                />
                <div className="approval-mode-selector__choice-content">
                  <div className="approval-mode-selector__choice-title">
                    <span
                      className={`approval-mode-selector__choice-name${
                        option.id === "full_access" ? " approval-mode-selector__choice-name--danger" : ""
                      }`}
                    >
                      {option.name}
                    </span>
                    {option.badge && (
                      <span
                        className={`approval-mode-selector__choice-badge${
                          option.id === "full_access" ? " approval-mode-selector__choice-badge--danger" : ""
                        }`}
                      >
                        {option.badge}
                      </span>
                    )}
                  </div>
                  <span className="approval-mode-selector__choice-desc">{option.description}</span>
                </div>
                {option.id === mode && (
                  <span className="approval-mode-selector__check" aria-hidden="true">✓</span>
                )}
              </label>
            ))}
          </fieldset>
        </div>
      )}

      <ConfirmDialog
        open={showRiskDialog}
        title="开启完全访问？"
        description={
          <div>
            <p style={{ margin: "0 0 0.5rem", fontWeight: 500 }}>Eidos 将不再逐项请求批准</p>
            <p style={{ margin: 0, color: "var(--muted)" }}>
              本次任务将以当前 macOS 用户的权限读写文件、运行命令和访问网络。操作可能造成数据丢失、凭据泄露或系统设置变化，也可能修改 Eidos 自身的数据。请仅在信任当前任务时开启。
            </p>
          </div>
        }
        confirmLabel="开启完全访问"
        cancelLabel="取消"
        isDestructive
        onConfirm={() => {
          onChange("full_access");
          setShowRiskDialog(false);
          triggerRef.current?.focus();
        }}
        onCancel={() => {
          setShowRiskDialog(false);
          triggerRef.current?.focus();
        }}
      />
    </div>
  );
}

function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
