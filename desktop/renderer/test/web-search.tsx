import { createRoot } from "react-dom/client";
import { SettingsPage } from "../src/components/settings/SettingsPage.js";
import type { WebSearchResponse } from "../../shared/web-search.generated.js";
import "../src/styles.css";

let saved: WebSearchResponse = { revision: 0, config: { defaultProvider: null }, credentials: { deepseek: "missing", langsearch: "missing" } };
const calls: unknown[] = [];
Object.assign(window, { webSearchFixtureCalls: calls });
window.eidosRuntime = {
  readWebSearch: async () => saved,
  saveWebSearch: async request => {
    calls.push(request);
    saved = { revision: saved.revision + 1, config: request.config, credentials: { deepseek: "stored", langsearch: "stored" } };
    return saved;
  },
  openWebSearchOfficialPage: async request => { calls.push(request); },
} as typeof window.eidosRuntime;
const noop = async () => {};
createRoot(document.getElementById("root")!).render(<SettingsPage initialCategory="web_search"
  runtime={{ state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: false, storageHealth: { state: "ready" } }}
  plugins={[]} skills={[]} mcpServers={[]} pendingAction={undefined} onClose={() => {}}
  onModelsChanged={noop} onImportPlugin={noop} onTogglePlugin={noop} onRemovePlugin={noop}
  onToggleSkill={noop} onRemoveSkill={async () => ({ qualifiedId: "fixture", removed: true, cleanupPending: false })}
  onToggleMcp={noop} onCreateMcp={noop} onUpdateMcp={noop} onRemoveMcp={noop}/>);
