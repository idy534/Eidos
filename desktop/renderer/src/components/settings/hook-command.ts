import type { HookDefinition } from "../../../../shared/hooks.js";

type ReviewedHook = Pick<HookDefinition, "handlerType" | "command" | "commandWindows" | "mcpServer" | "mcpTool" | "mcpInput">;

/**
 * The text a user reviews before trusting a Hook. Windows runs
 * `commandWindows` when it is set, so trust must show it alongside `command`.
 */
export function hookCommandText(hook: ReviewedHook, missingSuffix = ""): string {
  if (hook.handlerType === "mcp_tool") {
    return JSON.stringify({ server: hook.mcpServer, tool: hook.mcpTool, input: hook.mcpInput }, null, 2);
  }
  const command = hook.command || "";
  const windows = hook.commandWindows || "";
  if (windows && windows !== command) {
    return command ? `macOS：${command}\nWindows：${windows}` : `Windows：${windows}`;
  }
  return command || `处理器：${hook.handlerType}${missingSuffix}`;
}
