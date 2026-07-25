import { ReactNode } from "react";

export type PrimaryActionButtonSize = "compact" | "large";

export interface PrimaryActionButtonProps {
  size: PrimaryActionButtonSize;
  label: string;
  subtitle?: string;
  shortcut?: string;
  icon?: ReactNode;
  showArrow?: boolean;
  disabled?: boolean;
  loading?: boolean;
  loadingText?: string;
  onClick?: () => void;
  className?: string;
  id?: string;
  title?: string;
}

export function PrimaryActionButton({
  size,
  label,
  subtitle,
  shortcut,
  icon,
  showArrow = size === "large",
  disabled,
  loading,
  loadingText,
  onClick,
  className = "",
  id,
  title,
}: PrimaryActionButtonProps) {
  const displayLabel = loading
    ? (loadingText ?? (size === "compact" ? "正在创建…" : "正在打开…"))
    : label;

  return (
    <button
      type="button"
      className={`primary-action-btn primary-action-btn--${size}${className ? ` ${className}` : ""}`}
      disabled={disabled || loading}
      aria-busy={loading ? true : undefined}
      onClick={onClick}
      id={id}
      title={title}
    >
      <span className="primary-action-main">
        {loading ? (
          <SpinnerIcon />
        ) : (
          icon ?? (size === "compact" ? <PlusIcon /> : <FolderIcon />)
        )}
        <span className="primary-action-text-group">
          <span className="primary-action-label">{displayLabel}</span>
          {size === "large" && subtitle && !loading && (
            <span className="primary-action-subtitle">{subtitle}</span>
          )}
        </span>
      </span>

      {shortcut && size === "compact" && !loading && (
        <span className="primary-action-shortcut" aria-hidden="true">
          {shortcut}
        </span>
      )}

      {size === "large" && showArrow && !loading && (
        <ArrowIcon />
      )}
    </button>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true" className="primary-action-icon">
      <path d="M8 3.5V12.5M3.5 8H12.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 20 20" width="18" height="18" fill="none" aria-hidden="true" className="primary-action-icon">
      <path
        d="M2.5 4.75C2.5 3.78 3.28 3 4.25 3H7.8C8.3 3 8.77 3.22 9.08 3.6L10.3 5H15.75C16.72 5 17.5 5.78 17.5 6.75V15.25C17.5 16.22 16.72 17 15.75 17H4.25C3.28 17 2.5 16.22 2.5 15.25V4.75Z"
        fill="currentColor"
        fillOpacity="0.18"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true" className="primary-action-arrow">
      <path d="M3.5 8H12.5M8.5 4L12.5 8L8.5 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true" className="primary-action-spinner">
      <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.8" />
      <path d="M8 2.5C11.0376 2.5 13.5 4.96243 13.5 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
