import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const base: IconProps = {
  viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.4,
  strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true",
};

/** Goal mark shared by the status card and Feed markers. */
export function GoalMarkIcon(props: IconProps) {
  return <svg {...base} {...props}><circle cx="8" cy="8" r="6" /><circle cx="8" cy="8" r="3.1" /><circle cx="8" cy="8" r="0.9" fill="currentColor" stroke="none" /></svg>;
}

export function GoalPauseIcon(props: IconProps) {
  return <svg {...base} {...props}><path d="M6 4v8M10 4v8" /></svg>;
}

export function GoalPlayIcon(props: IconProps) {
  return <svg {...base} {...props}><path d="M5.5 3.8v8.4L12 8 5.5 3.8Z" /></svg>;
}

export function GoalCheckIcon(props: IconProps) {
  return <svg {...base} {...props}><circle cx="8" cy="8" r="6" /><path d="m5.4 8.2 1.8 1.8 3.4-3.8" /></svg>;
}

export function GoalBlockedIcon(props: IconProps) {
  return <svg {...base} {...props}><circle cx="8" cy="8" r="6" /><path d="M8 5v3.4M8 10.8v.1" /></svg>;
}

export function GoalPausedIcon(props: IconProps) {
  return <svg {...base} {...props}><circle cx="8" cy="8" r="6" /><path d="M6.6 5.8v4.4M9.4 5.8v4.4" /></svg>;
}

/** Automatic continuation: one more lap around the goal. */
export function GoalLoopIcon(props: IconProps) {
  return <svg {...base} {...props}><path d="M12.5 6.2A4.7 4.7 0 0 0 3.6 6.6M3.5 9.8a4.7 4.7 0 0 0 8.9.4" /><path d="M12.8 3.6v2.8H10M3.2 12.4V9.6H6" /></svg>;
}

export function GoalTokenIcon(props: IconProps) {
  return <svg {...base} {...props}><ellipse cx="8" cy="4.6" rx="4.6" ry="1.9" /><path d="M3.4 4.6v3.4c0 1 2 1.9 4.6 1.9s4.6-.9 4.6-1.9V4.6M3.4 8v3.4c0 1 2 1.9 4.6 1.9s4.6-.9 4.6-1.9V8" /></svg>;
}

export function GoalClockIcon(props: IconProps) {
  return <svg {...base} {...props}><circle cx="8" cy="8" r="6" /><path d="M8 4.8V8l2.2 1.4" /></svg>;
}

export function GoalClearIcon(props: IconProps) {
  return <svg {...base} {...props}><path d="M3 4.5h10M6.3 4.5V3h3.4v1.5M4.4 4.5l.6 8.5h6l.6-8.5" /></svg>;
}
