import { createRoot } from "react-dom/client";
import { SettingsPage } from "../src/components/settings/SettingsPage.js";
import type { ShellEnvironmentResponse } from "../../shared/shell-environment.generated.js";
import "../src/styles.css";

let saved: ShellEnvironmentResponse = { revision: 0, policy: { inherit: "all" } };
const calls: unknown[] = [];
Object.assign(window, { shellEnvironmentFixtureCalls: calls });
window.eidosRuntime = {
  readShellEnvironment: async () => saved,
  saveShellEnvironment: async request => {
    calls.push(request); saved = { revision: saved.revision + 1, policy: request.policy }; return saved;
  },
  refreshShellEnvironment: async () => { calls.push("refresh"); return saved; },
} as typeof window.eidosRuntime;
const noop = async () => {};
createRoot(document.getElementById("root")!).render(<SettingsPage initialCategory="runtime"
  runtime={{ state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: false, storageHealth: { state: "ready" } }}
  plugins={[]} skills={[]} mcpServers={[]} pendingAction={undefined} onClose={() => {}}
  onModelsChanged={noop} onImportPlugin={noop} onTogglePlugin={noop} onRemovePlugin={noop}
  onToggleSkill={noop} onRemoveSkill={async () => ({ qualifiedId: "fixture", removed: true, cleanupPending: false })}
  onToggleMcp={noop} onCreateMcp={noop} onUpdateMcp={noop} onRemoveMcp={noop}/>);
