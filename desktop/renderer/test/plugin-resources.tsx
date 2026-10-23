import { useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsPage } from "../src/components/settings/SettingsPage.js";
import type { EidosRuntimeAPI, McpServerRecord, PluginRecord, SkillMetadata } from "../src/contracts.js";
import "../src/styles.css";
import "../src/components/settings/SettingsPage.css";

const plugin: PluginRecord = {
  schemaVersion: 1, id: "checks", name: "代码质量检查", version: "1.0.0", description: "此插件包含代码审查 Skill、工具服务和任务完成检查。",
  contentHash: "a".repeat(64), enabled: true, status: "installed", installedAt: 1, updatedAt: 1,
  compatibility: { format: "codex", declaredVersion: "1.0.0", issues: ["codex_apps_not_supported"] },
  hookReview: { contentHash: "a".repeat(64), trusted: false, executableCount: 1,
    installedRoot: "/plugins/checks", dataDirectory: "/plugins/checks/hook-data",
    definitions: [{ key: "stop", event: "Stop", handlerType: "command", command: '"$RUNTIME_PYTHON" "$PLUGIN_ROOT/hooks/check_workspace_before_completion.py"' }] },
};
const initialSkill: SkillMetadata = {
  schemaVersion: 1, qualifiedId: "checks:review", name: "代码审查", description: "检查代码的正确性、权限、取消和恢复，并给出有证据的问题。",
  sourceKind: "plugin", pluginId: "checks", pluginVersion: "1.0.0", pluginHash: plugin.contentHash,
  contentHash: "b".repeat(64), enabled: true, available: true,
};
const mcp: McpServerRecord = {
  schemaVersion: 1, pluginId: "checks", pluginVersion: "1.0.0", pluginHash: plugin.contentHash, serverId: "local-quality-tools",
  executable: "python3", argv: ["tools.py"], envNames: [], permissionProfile: "workspace_read", startupTimeoutSeconds: 5,
  toolTimeoutSeconds: 10, declaredEnabled: true, consented: false, available: false, updatedAt: 1,
};
Object.assign(window, { eidosRuntime: {
  readSkillDetail: async () => ({ qualifiedId: initialSkill.qualifiedId, content: "# 审查清单\n逐项检查当前代码。", body: "# 审查清单\n逐项检查当前代码。", directory: "/plugins/checks/skills/review" }),
  readMcpTools: async () => ({ state: "not_authorized", tools: [] }),
} as unknown as EidosRuntimeAPI });

function Fixture() {
  const [skill, setSkill] = useState(initialSkill);
  return <SettingsPage initialCategory="plugins"
    runtime={{ state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: true, storageHealth: { state: "ready" } }}
    plugins={[plugin]} skills={[skill]} mcpServers={[mcp]} pendingAction={undefined}
    onClose={() => {}} onModelsChanged={async () => {}} onImportPlugin={async () => {}} onTogglePlugin={async () => {}}
    onTrustPluginHooks={async () => {}} onRemovePlugin={async () => {}}
    onToggleSkill={async (_id, enabled) => setSkill(value => ({ ...value, enabled }))}
    onRemoveSkill={async id => ({ qualifiedId: id, removed: true, cleanupPending: false })}
    onToggleMcp={async () => {}} onCreateMcp={async () => {}} onUpdateMcp={async () => {}} onRemoveMcp={async () => {}} />;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
