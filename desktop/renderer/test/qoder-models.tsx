import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { ModelOption } from "../src/contracts.js";
import { ModelSelector } from "../src/components/ModelSelector.js";
import { ModelSettings } from "../src/components/settings/ModelSettings.js";
import "../src/styles.css";

let phase = "idle";
const catalog = [
  { id: "efficient", name: "Efficient", enabled: true, contextWindows: [200000, 400000, 1000000], defaultContextWindow: 200000, maxOutputTokens: 32000, supportsImages: true, reasoning: { selections: ["low", "high", "max"], defaultSelection: "high" } },
  { id: "dfmodel", name: "DeepSeek-Flash", enabled: true, supportsImages: false },
  { id: "kmodel_latest", name: "Kimi-K3.1-Pro", enabled: true, supportsImages: true },
];
Object.assign(window, { qoderFixturePhase: (value: string) => { phase = value; } });
window.eidosRuntime = {
  listModelPresets: async () => ({ providers: [{ id: "deepseek", name: "深度求索", models: [{ id: "builtin", name: "内置模型", url: "https://fixture.invalid/chat/completions", supportsToolCall: true, supportsImages: false, supportsReasoning: false }] }] }),
  qoderConnection: async request => {
    if (request.action === "login") phase = "logging_in";
    if (request.action === "check") {
      phase = "checking";
      setTimeout(() => { phase = "ready"; }, 200);
    }
    if (request.action === "refresh") phase = "ready";
    return { resourceReady: true, phase, models: catalog,
      errorCode: phase === "error" ? "authentication_failed" : null };
  },
  createModel: async input => ({ id: "qoder-fixture", ...input }),
} as typeof window.eidosRuntime;
function Fixture() {
  const [models, setModels] = useState<ModelOption[]>(["dfmodel", "kmodel_latest"].map(modelId => ({
    id: `qoder-${modelId}`, modelId, name: modelId, provider: "qoder", vendor: "Qoder", supportsToolCall: true,
    supportsImages: false, supportsReasoning: false, qoder: { contextWindowTokens: 128000, maxOutputTokens: 8192 },
  })));
  return <>
    <ModelSettings storageHealthReady modelList={{ models }} onModelsChanged={async () => {
      setModels(current => current.map(model => ({ ...model, name: catalog.find(entry => entry.id === model.modelId)?.name ?? model.name })));
    }} onShowToast={() => {}} />
    <ModelSelector models={models} selectedModelId={models[0]!.id} disabled={false} onModelChange={() => {}} onChange={() => {}} />
  </>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
