export type SettingsCategory = "model" | "plugins" | "skills" | "mcp" | "worktrees" | "runtime";

export type SettingsPendingAction =
  | { type: "configure_model" }
  | { type: "import_plugin" }
  | { type: "toggle_plugin"; pluginId: string }
  | { type: "remove_plugin"; pluginId: string }
  | { type: "toggle_mcp"; pluginId: string; serverId: string }
  | undefined;

export interface SettingsToast {
  id: string;
  type: "success" | "info" | "error";
  message: string;
}
