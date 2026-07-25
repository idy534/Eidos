import React from "react";

export type StatusTone = "success" | "warning" | "danger" | "neutral" | "info";

interface StatusBadgeProps {
  tone?: StatusTone;
  children: React.ReactNode;
  className?: string;
  dot?: boolean;
}

export function StatusBadge({
  tone = "neutral",
  children,
  className = "",
  dot = true,
}: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge--${tone} ${className}`}>
      {dot && <span className="status-badge-dot" aria-hidden="true" />}
      <span className="status-badge-text">{children}</span>
    </span>
  );
}
