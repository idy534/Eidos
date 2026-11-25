export type SlashCommandId =
  | "plan"
  | "goal"
  | "team"
  | "compact"
  | "fork"
  | "review"
  | "status"
  | "model"
  | "mcp"
  | "plugins";

export interface SlashCommandDefinition {
  id: SlashCommandId;
  label: string;
  detail: string;
  keywords?: readonly string[];
}

export const SLASH_COMMANDS: readonly SlashCommandDefinition[] = [
  { id: "plan", label: "/plan", detail: "开始 Plan 模式", keywords: ["plan", "计划"] },
  { id: "goal", label: "/goal", detail: "设定持续推进的目标", keywords: ["goal", "目标"] },
  { id: "team", label: "/team", detail: "组建 Agent 团队", keywords: ["team", "团队"] },
  { id: "compact", label: "/compact", detail: "压缩当前上下文", keywords: ["compact", "压缩", "上下文"] },
  { id: "fork", label: "/fork", detail: "从最近回答分叉新对话", keywords: ["fork", "分叉"] },
  { id: "review", label: "/review", detail: "打开当前变更审阅", keywords: ["review", "diff", "审阅"] },
  { id: "status", label: "/status", detail: "查看当前会话状态", keywords: ["status", "状态"] },
  { id: "model", label: "/model", detail: "切换当前模型或思考强度", keywords: ["model", "模型"] },
  { id: "mcp", label: "/mcp", detail: "查看与管理 MCP", keywords: ["mcp"] },
  { id: "plugins", label: "/plugins", detail: "管理 Plugin", keywords: ["plugins", "插件"] },
];

const COMMAND_IDS = new Set<string>(SLASH_COMMANDS.map((cmd) => cmd.id));

export function matchExactSlashCommand(input: string): SlashCommandId | undefined {
  const trimmed = input.trim();
  if (!trimmed.startsWith("/") || trimmed.length < 2) return undefined;
  const candidate = trimmed.slice(1).toLowerCase();
  return COMMAND_IDS.has(candidate) ? (candidate as SlashCommandId) : undefined;
}
