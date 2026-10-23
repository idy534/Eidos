import { useState } from "react";
import { createRoot } from "react-dom/client";
import { PluginSettings } from "../src/components/settings/PluginSettings.js";
import type { PluginRecord } from "../src/contracts.js";
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

function Fixture() {
  const [plugin, setPlugin] = useState(initial);
  return <PluginSettings plugins={[plugin]} pendingAction={undefined} onImportPlugin={async () => {}}
    onTogglePlugin={async (id, enabled) => { calls.push({ action: "enable", id, enabled }); setPlugin(value => ({ ...value, enabled })); }}
    onTrustPluginHooks={async (id, contentHash, trusted) => { calls.push({ action: "trust", id, contentHash, trusted }); setPlugin(value => ({ ...value, hookReview: { ...value.hookReview!, trusted } })); }}
    onRemovePlugin={async () => {}} onShowToast={() => {}} />;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
