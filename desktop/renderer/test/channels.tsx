import { createRoot } from "react-dom/client";
import type { Channel, ChannelResponse, ChannelSaveRequest } from "../../shared/channels.js";
import type { ModelOption } from "../src/contracts.js";
import { SettingsPage } from "../src/components/settings/SettingsPage.js";
import "../src/styles.css";

declare global { interface Window { channelSaved: ChannelSaveRequest | undefined; } }
const initial: ChannelResponse = { state: "connected", connection: { id: "connection", revision: 1, hasSecret: true, clientSecret: "ding-sample-secret",
  config: { enabled: true, clientId: "ding-example", modelId: "deepseek-v4-flash", approvalMode: "manual" } },
  deliveries: [{ id: "reply", inboundId: "message", kind: "final:0", status: "unknown", text: "任务已完成，请在 Eidos 查看产物。",
    reason: "网络中断，发送结果未知。", createdAt: 1 }] };
const responses: Partial<Record<Channel, ChannelResponse>> = { dingtalk: initial };
window.eidosRuntime = { readChannel: async (request = {}) => responses[request.channel ?? "dingtalk"] ?? { state: "disabled" }, listProjects: async () => ({ items: [] }),
  saveChannel: async (request: ChannelSaveRequest) => {
    window.channelSaved = request;
    const channel = request.channel ?? "dingtalk";
    const previous = responses[channel];
    const response: ChannelResponse = { ...previous, state: request.config.enabled ? "connecting" : "disabled", connection: {
      channel, id: channel, revision: (previous?.connection?.revision ?? 0) + 1, hasSecret: true, clientSecret: request.clientSecret, config: request.config,
    } };
    responses[channel] = response;
    return response;
  },
} as unknown as typeof window.eidosRuntime;
const model = { id: "deepseek-v4-flash", name: "DeepSeek V4 Flash", provider: "deepseek", supportsReasoning: true, reasoning: { defaultSelection: "high", selections: ["none", "high"] } } as ModelOption;
const noop = async () => {};
createRoot(document.getElementById("root")!).render(<SettingsPage initialCategory="channels"
  runtime={{ state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: true, storageHealth: { state: "ready" } }}
  modelList={{ models: [model] }} plugins={[]} skills={[]} mcpServers={[]} pendingAction={undefined}
  onClose={() => {}} onModelsChanged={noop} onImportPlugin={noop} onTogglePlugin={noop} onRemovePlugin={noop}
  onToggleSkill={noop} onRemoveSkill={async () => ({ qualifiedId: "", removed: false })}
  onToggleMcp={noop} onCreateMcp={noop} onUpdateMcp={noop} onRemoveMcp={noop}/>);
