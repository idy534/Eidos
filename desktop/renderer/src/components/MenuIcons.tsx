import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const defaultIconProps: IconProps = {
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": "true",
  className: "menu-item-icon-svg",
};

/**
 * macOS Finder icon (or Folder open in Finder)
 */
export function FinderIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M2 3.5C2 2.67 2.67 2 3.5 2H6.2C6.6 2 6.98 2.16 7.26 2.44L8.56 3.74C8.84 4.02 9.22 4.18 9.62 4.18H12.5C13.33 4.18 14 4.85 14 5.68V12.5C14 13.33 13.33 14 12.5 14H3.5C2.67 14 2 13.33 2 12.5V3.5Z" />
      <path d="M6 9.5H10M8 7.5V11.5" />
    </svg>
  );
}

/**
 * Edit / Rename Pencil icon
 */
export function EditIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M11.3 2.7a1.41 1.41 0 0 1 2 2L4.7 13.3 2 14l.7-2.7L11.3 2.7z" />
      <path d="m10 4 2 2" />
    </svg>
  );
}

/**
 * Trash / Delete / Remove icon
 */
export function TrashIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M3 4.5h10M5.5 4.5V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5M12 4.5v8a1.5 1.5 0 0 1-1.5 1.5h-5A1.5 1.5 0 0 1 4 12.5v-8" />
      <path d="M6.5 7.5v4M9.5 7.5v4" />
    </svg>
  );
}

/**
 * Copy / Clipboard icon
 */
export function CopyIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <rect x="5.5" y="5.5" width="7.5" height="7.5" rx="1.5" />
      <path d="M3.5 10.5h-.5A1.5 1.5 0 0 1 1.5 9V3.5A1.5 1.5 0 0 1 3 2h5.5a1.5 1.5 0 0 1 1.5 1.5v.5" />
    </svg>
  );
}

/**
 * External link / Open in default app
 */
export function ExternalLinkIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M12 9v4a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h4" />
      <path d="M9.5 2H14v4.5" />
      <path d="M6.5 9.5 13.8 2.2" />
    </svg>
  );
}

/**
 * Preview / Eye icon
 */
export function PreviewIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8s-2.5 4.5-6.5 4.5S1.5 8 1.5 8z" />
      <circle cx="8" cy="8" r="2" />
    </svg>
  );
}

/**
 * Git Branch icon
 */
export function GitBranchIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <circle cx="4" cy="4" r="1.8" />
      <circle cx="4" cy="12" r="1.8" />
      <circle cx="12" cy="5.5" r="1.8" />
      <path d="M4 5.8v4.4" />
      <path d="M4 8.5a4 4 0 0 1 4-4h2.2" />
    </svg>
  );
}

/**
 * Send / Review feedback paper plane icon
 */
export function SendIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="m14 2-6 12-2.5-4.5L1 7l13-5z" />
      <path d="m5.5 9.5 4-4" />
    </svg>
  );
}

/**
 * Checkmark icon for single-select or checkable items
 */
export function CheckmarkIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="m3.5 8.5 3 3 6-7" />
    </svg>
  );
}

/**
 * Close Tab '×' icon
 */
export function CloseIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

/**
 * Close other tabs icon
 */
export function CloseOthersIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <rect x="2" y="3" width="9" height="10" rx="1.5" />
      <path d="M5 1h7a1.5 1.5 0 0 1 1.5 1.5V11" />
      <path d="m4.5 6.5 4 4M8.5 6.5l-4 4" />
    </svg>
  );
}

/**
 * Clock / History icon for "最近一轮"
 */
export function HistoryIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5V8l2.5 1.5" />
    </svg>
  );
}

/**
 * File changes / Diff icon for "未提交"
 */
export function FileDiffIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M9.5 2H4a1.5 1.5 0 0 0-1.5 1.5v9A1.5 1.5 0 0 0 4 14h8a1.5 1.5 0 0 0 1.5-1.5V5.5L9.5 2z" />
      <path d="M9 2v4h4" />
      <path d="M5.5 9h5M5.5 11.5h3" />
    </svg>
  );
}

/**
 * Task scope / Stack icon for "整个任务"
 */
export function TaskScopeIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <polygon points="8 2 14 5 8 8 2 5 8 2" />
      <path d="m2 8.5 6 3 6-3" />
      <path d="m2 12 6 3 6-3" />
    </svg>
  );
}

/**
 * Plus / Add icon
 */
export function PlusIcon(props: IconProps) {
  return (
    <svg {...defaultIconProps} {...props}>
      <path d="M8 3.5v9M3.5 8h9" />
    </svg>
  );
}
