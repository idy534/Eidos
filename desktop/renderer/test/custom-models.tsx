import { createRoot } from "react-dom/client";
import { ModelSettings } from "../src/components/settings/ModelSettings.js";
import "../src/styles.css";

const calls: unknown[] = [];
Object.assign(window, { customModelCalls: calls });
window.eidosRuntime = {
  listModelPresets: async () => ({ providers: [{ id: "deepseek", name: "深度求索 / DeepSeek", models: [{ id: "builtin", name: "内置模型", url: "https://fixture.invalid/chat/completions", supportsToolCall: true, supportsImages: false, supportsReasoning: false }] }] }),
  discoverCustomModels: async request => { calls.push(request); await new Promise(resolve => setTimeout(resolve, 400)); return { state: "success", message: "已获取 2 个模型", models: [
    { id: "company/deepseek-flash", name: "公司推理模型", contextWindowTokens: 128000, maxOutputTokens: 8192, supportsImages: true, reasoningSelections: ["low", "high", "max"] },
    { id: "company/text-model", name: "文本模型" },
  ] }; },
  createModel: async input => { calls.push(input); return { id: "custom-fixture", ...input }; },
} as typeof window.eidosRuntime;
createRoot(document.getElementById("root")!).render(<ModelSettings storageHealthReady modelList={{ models: [] }} onModelsChanged={async () => {}} onShowToast={() => {}} />);
