import type { CSSProperties, ReactNode } from "react";

export interface FieldErrorProps {
  /** The error message string or React node */
  error?: ReactNode;
  /** Alternate children syntax for error content */
  children?: ReactNode;
  /** Custom additional class name */
  className?: string;
  /** Inline CSS styles */
  style?: CSSProperties;
  /** ARIA accessibility role (default: "alert") */
  role?: string;
  /** HTML tag to render (default: "p", or "span" if inline is true) */
  as?: "p" | "span" | "div" | "small" | "li";
  /** Whether to render as an inline element (default: false) */
  inline?: boolean;
  /** Whether to show the subtle warning icon (default: true) */
  icon?: boolean;
}

/**
 * Standardized field-level and section-level error feedback component.
 * Ensures consistent red tone, icon, typography, and ARIA role="alert" across all dialogs and forms.
 */
export function FieldError({
  error,
  children,
  className = "",
  style,
  role = "alert",
  as,
  inline = false,
  icon = true,
}: FieldErrorProps) {
  const content = error ?? children;
  if (!content) return null;

  const Tag = as ?? (inline ? "span" : "p");
  const classes = [
    "field-error",
    inline ? "field-error--inline" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Tag className={classes} role={role} style={style}>
      {icon && (
        <svg
          className="field-error__icon"
          width="12"
          height="12"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="8" cy="8" r="7" />
          <line x1="8" y1="4.5" x2="8" y2="8.5" />
          <circle cx="8" cy="11.5" r="0.5" fill="currentColor" />
        </svg>
      )}
      <span className="field-error__text">{content}</span>
    </Tag>
  );
}
