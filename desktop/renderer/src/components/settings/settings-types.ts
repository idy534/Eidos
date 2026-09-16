export type SettingsCategory = "model" | "plugins" | "skills" | "mcp" | "worktrees" | "runtime";

export type SettingsPendingAction =
  | { type: "configure_model" }
  | { type: "toggle_skill"; qualifiedId: string }
  | { type: "remove_skill"; qualifiedId: string }
  | { type: "import_plugin" }
  | { type: "toggle_plugin"; pluginId: string }
  | { type: "remove_plugin"; pluginId: string }
  | { type: "toggle_mcp"; pluginId: string; serverId: string }
  | { type: "create_mcp" }
  | undefined;

export interface SettingsToast {
  id: string;
  type: "success" | "info" | "error";
  message: string;
}
