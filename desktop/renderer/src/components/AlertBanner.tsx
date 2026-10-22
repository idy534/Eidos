import React from "react";

export type AlertTone = "info" | "warning" | "error" | "success";

export interface AlertBannerProps {
  tone?: AlertTone;
  title?: React.ReactNode;
  children?: React.ReactNode;
  action?: React.ReactNode;
  onClose?: () => void;
  className?: string;
  role?: "alert" | "status";
}

function AlertIcon({ tone }: { tone: AlertTone }) {
  switch (tone) {
    case "error":
      return (
        <svg
          viewBox="0 0 16 16"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="8" cy="8" r="6" />
          <line x1="8" y1="5" x2="8" y2="8.5" />
          <circle cx="8" cy="11" r="0.5" fill="currentColor" stroke="none" />
        </svg>
      );
    case "warning":
      return (
        <svg
          viewBox="0 0 16 16"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M8 2.5 14 13H2L8 2.5z" />
          <line x1="8" y1="6.5" x2="8" y2="9.5" />
          <circle cx="8" cy="11.5" r="0.5" fill="currentColor" stroke="none" />
        </svg>
      );
    case "success":
      return (
        <svg
          viewBox="0 0 16 16"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="8" cy="8" r="6" />
          <path d="m5.5 8 2 2 3.5-4" />
        </svg>
      );
    case "info":
    default:
      return (
        <svg
          viewBox="0 0 16 16"
          width="16"
          height="16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="8" cy="8" r="6" />
          <line x1="8" y1="7.5" x2="8" y2="11.5" />
          <circle cx="8" cy="5" r="0.5" fill="currentColor" stroke="none" />
        </svg>
      );
  }
}

export function AlertBanner({
  tone = "info",
  title,
  children,
  action,
  onClose,
  className = "",
  role,
}: AlertBannerProps) {
  const defaultRole = tone === "error" || tone === "warning" ? "alert" : "status";

  return (
    <div
      role={role ?? defaultRole}
      className={`alert-banner alert-banner--${tone} ${className}`.trim()}
    >
      <div className="alert-banner__icon" aria-hidden="true">
        <AlertIcon tone={tone} />
      </div>
      <div className="alert-banner__content">
        {title && <div className="alert-banner__title">{title}</div>}
        {children && <div className="alert-banner__message">{children}</div>}
      </div>
      {action && <div className="alert-banner__action">{action}</div>}
      {onClose && (
        <button
          type="button"
          className="alert-banner__close"
          aria-label="关闭提示"
          onClick={onClose}
        >
          <svg
            viewBox="0 0 16 16"
            width="12"
            height="12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <line x1="4" y1="4" x2="12" y2="12" />
            <line x1="12" y1="4" x2="4" y2="12" />
          </svg>
        </button>
      )}
    </div>
  );
}
