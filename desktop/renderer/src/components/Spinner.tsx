

export type SpinnerSize = "sm" | "md" | "lg";
export type SpinnerTone = "current" | "accent" | "muted" | "white";

export interface SpinnerProps {
  size?: SpinnerSize;
  tone?: SpinnerTone;
  className?: string;
  ariaLabel?: string;
}

const SIZE_MAP: Record<SpinnerSize, number> = {
  sm: 14,
  md: 18,
  lg: 24,
};

export function Spinner({
  size = "md",
  tone = "current",
  className = "",
  ariaLabel = "正在加载",
}: SpinnerProps) {
  const dimension = SIZE_MAP[size];

  return (
    <span
      role="status"
      aria-label={ariaLabel}
      className={`spinner spinner--${size} spinner--${tone} ${className}`.trim()}
    >
      <svg
        viewBox="0 0 16 16"
        width={dimension}
        height={dimension}
        fill="none"
        aria-hidden="true"
      >
        <circle
          cx="8"
          cy="8"
          r="6"
          stroke="currentColor"
          strokeWidth="2"
          opacity="0.25"
        />
        <path
          d="M8 2a6 6 0 0 1 6 6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}
