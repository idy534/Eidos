import { forwardRef, type ReactNode, type ButtonHTMLAttributes } from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "small" | "medium" | "large";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  /** Icon to render before the label */
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}

/**
 * Unified Button component.
 *
 * Semantic guidelines:
 * - `primary`   — most important submit action on the page
 * - `secondary` — neutral secondary action (NOT red; use `danger` for destructive)
 * - `ghost`     — low-weight actions in toolbars and menus
 * - `danger`    — destructive or irreversible actions (delete, force-stop)
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "medium",
    loading = false,
    icon,
    children,
    disabled,
    className = "",
    type = "button",
    ...rest
  },
  ref,
) {
  const classes = [
    "btn",
    `btn--${variant}`,
    `btn--${size}`,
    loading ? "btn--loading" : "",
    className,
  ].filter(Boolean).join(" ");

  return (
    <button
      ref={ref}
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading ? true : undefined}
      {...rest}
    >
      {loading && (
        <svg
          className="btn-spinner"
          viewBox="0 0 16 16"
          width="14"
          height="14"
          fill="none"
          aria-hidden="true"
        >
          <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.8" />
          <path d="M8 2.5C11.0376 2.5 13.5 4.96243 13.5 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      )}
      {!loading && icon && (
        <span className="btn-icon" aria-hidden="true">{icon}</span>
      )}
      <span className="btn-label">{children}</span>
    </button>
  );
});
