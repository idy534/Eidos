import type { SVGProps } from "react";

type ExecutionModeIconProps = Omit<SVGProps<SVGSVGElement>, "viewBox" | "children"> & { size?: number };

/** "本地": the task runs directly in the project directory. */
export function LocalIcon({ size = 20, strokeWidth = 1.5, ...props }: ExecutionModeIconProps) {
  return (
    <svg viewBox="0 0 20 20" width={size} height={size} fill="none" aria-hidden="true" {...props}>
      <rect x="3.25" y="3.5" width="13.5" height="9" rx="1.5" fill="currentColor" fillOpacity=".12" stroke="currentColor" strokeWidth={strokeWidth} />
      <path d="M1.75 16h16.5" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" />
    </svg>
  );
}

/** "工作树": an independent Git worktree derived from the current checkout. */
export function WorktreeIcon({ size = 20, strokeWidth = 1.5, ...props }: ExecutionModeIconProps) {
  return (
    <svg viewBox="0 0 20 20" width={size} height={size} fill="none" aria-hidden="true" {...props}>
      <rect x="2.75" y="2.75" width="6.5" height="6.5" rx="1.5" stroke="currentColor" strokeWidth={strokeWidth} />
      <rect x="10.75" y="10.75" width="6.5" height="6.5" rx="1.5" fill="currentColor" fillOpacity=".12" stroke="currentColor" strokeWidth={strokeWidth} />
      <path d="M6 9.25V12a2 2 0 0 0 2 2h2.75" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
