export function compatibilityIssue(issue: string): string {
  if (issue.startsWith("hook_not_supported:")) return `当前版本不会执行此 Hook：${issue.slice("hook_not_supported:".length)}`;
  if (issue === "bundled_mcp_requires_separate_configuration") return "当前版本不会自动接入此插件的 MCP 声明。";
  if (issue === "codex_apps_not_supported") return "当前版本尚未接入此插件的 Codex App。";
  if (issue === "plugin_content_unavailable") return "插件文件已变化或无法读取。您可以停用、撤销信任或移除此插件。";
  return issue;
}
