import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PluginSettings } from "../src/components/settings/PluginSettings.js";
import type { McpServerRecord, PluginRecord } from "../src/contracts.js";
import "../src/styles.css";
import "../src/components/settings/SettingsPage.css";

const calls: unknown[] = [];
Object.assign(window, { pluginHookFixtureCalls: calls });
const initial: PluginRecord = {
  schemaVersion: 1, id: "local-checks", name: "本地质量检查", version: "1.0.0", description: "在任务完成前检查工作区。",
  contentHash: "a".repeat(64), enabled: false, status: "installed", installedAt: 1, updatedAt: 1,
  compatibility: { format: "codex", declaredVersion: "1.0.0", issues: ["hook_not_supported:hooks/hooks.json:SessionStart:0:0"] },
  hookReview: { contentHash: "a".repeat(64), trusted: false, executableCount: 1,
    installedRoot: "/Users/example/Library/Application Support/Eidos/extensions/plugins/local-checks/1.0.0/package",
    dataDirectory: "/Users/example/Library/Application Support/Eidos/extensions/plugins/local-checks/hook-data",
    definitions: [{ key: "stop", event: "Stop", handlerType: "command", command: '"$RUNTIME_PYTHON" "$PLUGIN_ROOT/hooks/check_workspace_before_completion.py" --configuration local-workspace-checks' }] },
};

const server: McpServerRecord = {
  schemaVersion: 1, pluginId: "local-checks", pluginVersion: "1.0.0", pluginHash: "a".repeat(64),
  serverId: "workspace-checks", executable: "${RUNTIME_PYTHON}", argv: ["${PLUGIN_ROOT}/servers/workspace_checks.py", "--stdio"],
  envNames: ["CHECKS_TOKEN"], permissionProfile: "workspace_read", startupTimeoutSeconds: 15, toolTimeoutSeconds: 60,
  declaredEnabled: true, consented: false, available: false, updatedAt: 0,
};

function Fixture() {
  const [plugin, setPlugin] = useState(initial);
  const [servers, setServers] = useState([server]);
  return <PluginSettings plugins={[plugin]} mcpServers={servers} pendingAction={undefined} onImportPlugin={async () => {}}
    onTogglePlugin={async (id, enabled) => { calls.push({ action: "enable", id, enabled }); setPlugin(value => ({ ...value, enabled })); }}
    onTrustPluginHooks={async (id, contentHash, trusted) => { calls.push({ action: "trust", id, contentHash, trusted }); setPlugin(value => ({ ...value, hookReview: { ...value.hookReview!, trusted } })); }}
    onActivatePlugin={async (id, contentHash) => {
      calls.push({ action: "activate", id, contentHash });
      setPlugin(value => ({ ...value, enabled: true, hookReview: { ...value.hookReview!, trusted: true } }));
      setServers(values => values.map(value => ({ ...value, consented: true, available: true })));
    }}
    onRemovePlugin={async () => {}} onShowToast={() => {}} />;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
