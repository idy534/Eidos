import React from "react";

export type BadgeVariant = "subtle" | "outline" | "solid" | "count";
export type BadgeTone =
  | "neutral"
  | "accent"
  | "danger"
  | "warning"
  | "success"
  | "info";

export interface BadgeProps {
  variant?: BadgeVariant;
  tone?: BadgeTone;
  children: React.ReactNode;
  className?: string;
  title?: string;
}

export function Badge({
  variant = "subtle",
  tone = "neutral",
  children,
  className = "",
  title,
}: BadgeProps) {
  return (
    <span
      className={`badge badge--${variant} badge--${tone} ${className}`.trim()}
      title={title}
    >
      {children}
    </span>
  );
}
