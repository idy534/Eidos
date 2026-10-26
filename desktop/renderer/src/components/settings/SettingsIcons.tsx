import type { SVGProps } from "react";
import type { SettingsCategory } from "./settings-types.js";
import { AgentIcon, SkillIcon, MemoryIcon } from "../InputPicker.js";
export const githubIcon =
  "data:image/png;base64,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";

export function GitHubIcon({
  className,
  width = 18,
  height = 18,
  alt = "GitHub",
  ...props
}: {
  className?: string;
  width?: number | string;
  height?: number | string;
  alt?: string;
} & React.ImgHTMLAttributes<HTMLImageElement>) {
  return (
    <img
      src={githubIcon}
      alt={alt}
      width={width}
      height={height}
      className={className}
      {...props}
    />
  );
}

type IconProps = SVGProps<SVGSVGElement>;

export function ModelSettingsIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M12 20v2" />
      <path d="M12 2v2" />
      <path d="M17 20v2" />
      <path d="M17 2v2" />
      <path d="M2 12h2" />
      <path d="M2 17h2" />
      <path d="M2 7h2" />
      <path d="M20 12h2" />
      <path d="M20 17h2" />
      <path d="M20 7h2" />
      <path d="M7 20v2" />
      <path d="M7 2v2" />
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="8" y="8" width="8" height="8" rx="1" />
    </svg>
  );
}

export function WebSearchSettingsIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
      <path d="M2 12h20" />
    </svg>
  );
}

export function AssistantsSettingsIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M16 10a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 14.286V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
      <path d="M20 9a2 2 0 0 1 2 2v10.286a.71.71 0 0 1-1.212.502l-2.202-2.202A2 2 0 0 0 17.172 19H10a2 2 0 0 1-2-2v-1" />
    </svg>
  );
}

export function MemorySettingsIcon({ className, width = 18, height = 18, ...props }: IconProps = {}) {
  return <MemoryIcon width={width} height={height} className={className} {...props} />;
}

export function PluginsSettingsIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z" />
    </svg>
  );
}

export function SkillsSettingsIcon({ className, width = 18, height = 18, ...props }: IconProps = {}) {
  return <SkillIcon width={width} height={height} className={className} {...props} />;
}

/**
 * 智能体设置图标：保持与应用其他地方一致的现有 AgentIcon
 */
export function AgentsSettingsIcon({ className }: { className?: string } = {}) {
  return className ? (
    <AgentIcon width={18} height={18} className={className} />
  ) : (
    <AgentIcon width={18} height={18} />
  );
}

export function McpSettingsIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 180 180"
      fill="none"
      stroke="currentColor"
      aria-hidden="true"
      {...props}
    >
      <path
        d="M18 84.8528L85.8822 16.9706C95.2548 7.59798 110.451 7.59798 119.823 16.9706V16.9706C129.196 26.3431 129.196 41.5391 119.823 50.9117L68.5581 102.177"
        strokeWidth={12}
        strokeLinecap="round"
      />
      <path
        d="M69.2652 101.47L119.823 50.9117C129.196 41.5391 144.392 41.5391 153.765 50.9117L154.118 51.2652C163.491 60.6378 163.491 75.8338 154.118 85.2063L92.7248 146.6C89.6006 149.724 89.6006 154.789 92.7248 157.913L105.331 170.52"
        strokeWidth={12}
        strokeLinecap="round"
      />
      <path
        d="M102.853 33.9411L52.6482 84.1457C43.2756 93.5183 43.2756 108.714 52.6482 118.087V118.087C62.0208 127.459 77.2167 127.459 86.5893 118.087L136.794 67.8822"
        strokeWidth={12}
        strokeLinecap="round"
      />
    </svg>
  );
}

export function WorktreeSettingsIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M15 6a9 9 0 0 0-9 9V3" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
    </svg>
  );
}

export function RuntimeSettingsIcon(props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="m7 11 2-2-2-2" />
      <path d="M11 13h4" />
      <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
    </svg>
  );
}

export function SettingsCategoryIcon({
  category,
  className,
}: {
  category: SettingsCategory;
  className?: string;
}) {
  const iconProps = className ? { className } : {};
  switch (category) {
    case "model":
      return <ModelSettingsIcon {...iconProps} />;
    case "web_search":
      return <WebSearchSettingsIcon {...iconProps} />;
    case "channels":
      return <AssistantsSettingsIcon {...iconProps} />;
    case "memory":
      return <MemorySettingsIcon {...iconProps} />;
    case "plugins":
      return <PluginsSettingsIcon {...iconProps} />;
    case "skills":
      return <SkillsSettingsIcon {...iconProps} />;
    case "agents":
      return <AgentsSettingsIcon {...iconProps} />;
    case "mcp":
      return <McpSettingsIcon {...iconProps} />;
    case "worktrees":
      return <WorktreeSettingsIcon {...iconProps} />;
    case "runtime":
      return <RuntimeSettingsIcon {...iconProps} />;
    default:
      return null;
  }
}
