import { createRoot } from "react-dom/client";
import { SettingsPage } from "../src/components/settings/SettingsPage.js";
import type { EidosRuntimeAPI } from "../src/contracts.js";
import type { PersonalizationResponse, PersonalizationSaveRequest } from "../../shared/personalization.js";
import "../src/styles.css";

let settings: PersonalizationResponse = { revision: 1, preferences: {
  responseLanguage: "zh-CN", tone: "direct", responseLength: "concise", outputFormat: "conclusion_first",
  preferredName: "小李", role: "产品经理与开发者", familiarTopics: "我熟悉 TypeScript 和产品设计，正在学习 Python。",
  customInstructions: "请使用短句。\n\n你给出建议时，请说明依据。你引用文件时，请提供可点击的链接。",
} };
const fixture = { failRead: new URLSearchParams(location.search).has("failRead"), failSave: false, saves: [] as PersonalizationSaveRequest[] };
Object.assign(window, { personalizationFixture: fixture });
window.eidosRuntime = {
  readPersonalization: async () => {
    await new Promise(resolve => setTimeout(resolve, 200));
    if (fixture.failRead) { fixture.failRead = false; throw new Error("fixture offline"); }
    return settings;
  },
  savePersonalization: async (request: PersonalizationSaveRequest) => {
    fixture.saves.push(request);
    await new Promise(resolve => setTimeout(resolve, 200));
    if (fixture.failSave) { fixture.failSave = false; throw new Error("fixture offline"); }
    settings = { revision: settings.revision + 1, preferences: request.preferences };
    return settings;
  },
} as unknown as EidosRuntimeAPI;
createRoot(document.getElementById("root")!).render(<SettingsPage initialCategory="personalization"
  runtime={{ state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: false, storageHealth: { state: "ready" } }}
  plugins={[]} skills={[]} mcpServers={[]} pendingAction={undefined} onClose={() => {}}
  onModelsChanged={async () => {}} onImportPlugin={async () => null} onTogglePlugin={async () => {}} onRemovePlugin={async () => {}}
  onToggleSkill={async () => {}} onRemoveSkill={async qualifiedId => ({ qualifiedId, removed: true, cleanupPending: false })}
  onToggleMcp={async () => {}} onCreateMcp={async () => {}} onUpdateMcp={async () => {}} onRemoveMcp={async () => {}} />);
