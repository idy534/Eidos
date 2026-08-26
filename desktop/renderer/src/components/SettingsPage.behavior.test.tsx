import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI, ModelListResult, ModelPresetsResult, RuntimeStatus } from "../contracts.js";
import { SettingsPage } from "./settings/SettingsPage.js";

const runtime: RuntimeStatus = {
  state: "ready", protocolVersion: 1, runtimeVersion: "0.3.0", runShell: true,
  modelConfigured: true, storageHealth: { state: "ready" },
};
const modelList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [{
    id: "deepseek-v4-flash", name: "DeepSeek-V4 Flash", vendor: "DeepSeek",
    provider: "deepseek", url: "https://api.deepseek.com/chat/completions",
    supportsToolCall: true, supportsImages: false, supportsReasoning: true,
    reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
  }],
};
const presets: ModelPresetsResult = {
  providers: [
    {
      id: "deepseek", name: "深度求索 / DeepSeek", models: [modelList.models[0]!],
    },
    {
      id: "minimax", name: "MiniMax", models: [{
        id: "MiniMax-M3", name: "MiniMax M3", url: "https://api.minimaxi.com/v1/chat/completions",
        supportsToolCall: true, supportsImages: false, supportsReasoning: true,
        reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
      }],
    },
    {
      id: "kimi", name: "月之暗面 / Kimi", models: [{
        id: "kimi-k3", name: "Kimi K3", url: "https://api.moonshot.cn/v1/chat/completions",
        supportsToolCall: true, supportsImages: false, supportsReasoning: true,
        reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
      }],
    },
    {
      id: "volcengine", name: "火山引擎 / Volcengine", models: [
        {
          id: "deepseek-v4-pro-ga-260813", name: "DeepSeek V4 Pro GA",
          url: "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
          supportsToolCall: true, supportsImages: false, supportsReasoning: false,
        },
        {
          id: "glm-5.3", name: "GLM 5.3",
          url: "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
          supportsToolCall: true, supportsImages: false, supportsReasoning: false,
        },
        {
          id: "minimax-m3", name: "MiniMax M3",
          url: "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
          supportsToolCall: true, supportsImages: true, supportsReasoning: false,
        },
      ],
    },
  ],
};
const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

function props(overrides = {}) {
  return {
    runtime,
    modelList,
    modelLoading: false,
    modelError: undefined,
    plugins: [], skills: [], mcpServers: [], pendingAction: undefined,
    onClose: vi.fn(), onModelsChanged: vi.fn().mockResolvedValue(undefined),
    onImportPlugin: vi.fn(), onTogglePlugin: vi.fn(), onRemovePlugin: vi.fn(),
    onToggleMcp: vi.fn(),
    ...overrides,
  };
}

describe("Model settings", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("shows one local configuration entry and no legacy profile or capability UI", async () => {
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props()} />);

    expect(await screen.findByText("本地配置文件")).toBeInTheDocument();
    expect(screen.getByText("管理写入 ~/.eidos/models.json")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek-V4 Flash")).toBeInTheDocument();
    expect(screen.queryByText(/Model Profiles|Test Connection|Capability|Verified|Unknown/)).not.toBeInTheDocument();
    expect(screen.queryByText("模型服务配置")).not.toBeInTheDocument();
  });

  it("creates a catalog model without exposing its real id in the select", async () => {
    const user = userEvent.setup();
    const createModel = vi.fn().mockResolvedValue(modelList.models[0]);
    const onModelsChanged = vi.fn().mockResolvedValue(undefined);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
      createModel,
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props({ modelList: { models: [], defaultModelId: null }, onModelsChanged })} />);

    await user.click(await screen.findByRole("button", { name: "添加模型" }));
    expect(screen.getByRole("heading", { name: "添加模型" })).toBeInTheDocument();
    expect(screen.getByText("仅支持 OpenAI 兼容协议 API")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("提供商"), "minimax");
    expect(screen.getByRole("option", { name: "MiniMax M3" })).not.toHaveTextContent("MiniMax-M3");
    await user.type(screen.getByLabelText("API Key"), "sk-local-secret");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(createModel).toHaveBeenCalledWith({
      provider: "minimax", modelId: "MiniMax-M3", apiKey: "sk-local-secret",
    });
    expect(onModelsChanged).toHaveBeenCalledTimes(1);
  });

  it("editing with an empty key keeps the existing key", async () => {
    const user = userEvent.setup();
    const updateModel = vi.fn().mockResolvedValue(modelList.models[0]);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets), updateModel,
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props()} />);

    await user.click(await screen.findByRole("button", { name: "编辑 DeepSeek-V4 Flash" }));
    expect(screen.getByPlaceholderText("留空表示保持原值")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(updateModel).toHaveBeenCalledWith({
      id: "deepseek-v4-flash", provider: "deepseek", modelId: "deepseek-v4-flash",
    });
  });

  it("creates a Volcengine Coding Plan model from the catalog", async () => {
    const user = userEvent.setup();
    const createModel = vi.fn().mockResolvedValue(modelList.models[0]);
    const onModelsChanged = vi.fn().mockResolvedValue(undefined);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
      createModel,
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props({ modelList: { models: [], defaultModelId: null }, onModelsChanged })} />);

    await user.click(await screen.findByRole("button", { name: "添加模型" }));
    await user.selectOptions(screen.getByLabelText("提供商"), "volcengine");
    expect(screen.getByRole("option", { name: "DeepSeek V4 Pro GA" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "GLM 5.3" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "MiniMax M3" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("API Key"), "volcengine-secret");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(createModel).toHaveBeenCalledWith({
      provider: "volcengine", modelId: "deepseek-v4-pro-ga-260813", apiKey: "volcengine-secret",
    });
    expect(onModelsChanged).toHaveBeenCalledTimes(1);
  });
});
