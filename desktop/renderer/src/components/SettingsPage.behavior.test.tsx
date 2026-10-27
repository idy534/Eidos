import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import deepseekLogo from "../assets/providers/deepseek.svg";
import kimiLogo from "../assets/providers/kimi.svg";
import minimaxLogo from "../assets/providers/minimax.svg";
import volcengineLogo from "../assets/providers/volcengine.svg";
import type {
  EidosRuntimeAPI,
  McpServerRecord,
  ModelListResult,
  ModelPresetsResult,
  RuntimeStatus,
} from "../contracts.js";
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
    reasoning: { defaultSelection: "high", selections: ["high", "max"] },
    apiKey: "sk-saved-deepseek-key",
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
        reasoning: { defaultSelection: "thinking", selections: ["none", "thinking"] },
      }],
    },
    {
      id: "kimi", name: "月之暗面 / Kimi", models: [{
        id: "kimi-k3", name: "Kimi K3", url: "https://api.moonshot.cn/v1/chat/completions",
        supportsToolCall: true, supportsImages: false, supportsReasoning: true,
        reasoning: { defaultSelection: "max", selections: ["low", "high", "max"] },
      }],
    },
    {
      id: "volcengine", name: "火山引擎 / Volcengine", models: [
        {
          id: "deepseek-v4-pro-ga-260813", name: "DeepSeek V4 Pro GA",
          url: "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
          supportsToolCall: true, supportsImages: false, supportsReasoning: false,
        },
        {
          id: "glm-5.3", name: "GLM 5.3",
          url: "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
          supportsToolCall: true, supportsImages: false, supportsReasoning: false,
        },
        {
          id: "glm-5.3-flash", name: "GLM 5.3 Flash",
          url: "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
          supportsToolCall: true, supportsImages: false, supportsReasoning: false,
        },
        {
          id: "kimi-k3.1-pro", name: "Kimi K3.1 Pro",
          url: "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
          supportsToolCall: true, supportsImages: true, supportsReasoning: true,
          reasoning: { defaultSelection: "max", selections: ["low", "high", "max"] },
        },
        {
          id: "minimax-m3", name: "MiniMax M3",
          url: "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
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
    onToggleSkill: vi.fn().mockResolvedValue(undefined),
    onRemoveSkill: vi.fn().mockResolvedValue({
      qualifiedId: "user:test", removed: true, cleanupPending: false,
    }),
    onToggleMcp: vi.fn(), onCreateMcp: vi.fn().mockResolvedValue(undefined),
    onUpdateMcp: vi.fn().mockResolvedValue(undefined), onRemoveMcp: vi.fn().mockResolvedValue(undefined),
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
    const { container } = render(<SettingsPage {...props()} />);

    expect(await screen.findByText("本地配置文件")).toBeInTheDocument();
    expect(screen.queryByText("自定义模型")).not.toBeInTheDocument();
    expect(screen.getByText("管理写入 ~/.eidos/models.json")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek-V4 Flash")).toBeInTheDocument();
    expect(screen.getByText("深度求索")).toBeInTheDocument();
    expect(container.querySelector("img.model-vendor-icon")).toHaveAttribute("src", deepseekLogo);
    expect(container.querySelector(".saved-model-copy strong + span")).toHaveTextContent("深度求索");
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
    const { container } = render(<SettingsPage {...props({ modelList: { models: [], defaultModelId: null }, onModelsChanged })} />);

    await user.click(await screen.findByRole("button", { name: "添加模型" }));
    expect(screen.getByRole("heading", { name: "添加模型" })).toBeInTheDocument();
    expect(screen.getByText("仅支持 OpenAI 兼容协议 API")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("提供商"), "kimi");
    expect(container.querySelector(".model-provider-control img")).toHaveAttribute("src", kimiLogo);
    await user.selectOptions(screen.getByLabelText("提供商"), "minimax");
    expect(container.querySelector(".model-provider-control img")).toHaveAttribute("src", minimaxLogo);
    expect(screen.getByRole("option", { name: "MiniMax M3" })).not.toHaveTextContent("MiniMax-M3");
    await user.type(screen.getByLabelText("API Key"), "sk-local-secret");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(createModel).toHaveBeenCalledWith({
      provider: "minimax", modelId: "MiniMax-M3", apiKey: "sk-local-secret",
    });
    expect(onModelsChanged).toHaveBeenCalledTimes(1);
  });

  it("edits an existing model, displays masked api key by default, reveals on click, and saves the key", async () => {
    const user = userEvent.setup();
    const updateModel = vi.fn().mockResolvedValue(modelList.models[0]);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets), updateModel,
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props()} />);

    await user.click(await screen.findByRole("button", { name: "编辑 DeepSeek-V4 Flash" }));
    const input = screen.getByLabelText("API Key");
    expect(input).toHaveValue("sk-saved-deepseek-key");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("placeholder", "请输入 API Key");

    const toggle = screen.getByRole("button", { name: "显示 API Key" });
    await user.click(toggle);
    expect(input).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "隐藏 API Key" }));
    expect(input).toHaveAttribute("type", "password");

    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(updateModel).toHaveBeenCalledWith({
      id: "deepseek-v4-flash", provider: "deepseek", modelId: "deepseek-v4-flash",
      apiKey: "sk-saved-deepseek-key",
    });
  });

  it("creates a Volcengine Agent Plan model from the catalog", async () => {
    const user = userEvent.setup();
    const createModel = vi.fn().mockResolvedValue(modelList.models[0]);
    const onModelsChanged = vi.fn().mockResolvedValue(undefined);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
      createModel,
    } as EidosRuntimeAPI;
    const { container } = render(<SettingsPage {...props({ modelList: { models: [], defaultModelId: null }, onModelsChanged })} />);

    await user.click(await screen.findByRole("button", { name: "添加模型" }));
    await user.selectOptions(screen.getByLabelText("提供商"), "volcengine");
    expect(container.querySelector(".model-provider-control img")).toHaveAttribute("src", volcengineLogo);
    expect(screen.getByRole("option", { name: "DeepSeek V4 Pro GA" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "GLM 5.3" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "GLM 5.3 Flash" }),
    ).not.toHaveTextContent("glm-5.3-flash");
    expect(screen.getByRole("option", { name: "MiniMax M3" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("模型名称"), "glm-5.3-flash");
    await user.type(screen.getByLabelText("API Key"), "volcengine-secret");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(createModel).toHaveBeenCalledWith({
      provider: "volcengine", modelId: "glm-5.3-flash", apiKey: "volcengine-secret",
    });
    expect(onModelsChanged).toHaveBeenCalledTimes(1);
  });

  it("offers Kimi K3.1 Pro under Volcengine and saves its model ID", async () => {
    const user = userEvent.setup();
    const createModel = vi.fn().mockResolvedValue(modelList.models[0]);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets), createModel,
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props({ modelList: { models: [], defaultModelId: null } })} />);

    await user.click(await screen.findByRole("button", { name: "添加模型" }));
    await user.selectOptions(screen.getByLabelText("提供商"), "volcengine");
    expect(screen.getByRole("option", { name: "Kimi K3.1 Pro" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("模型名称"), "kimi-k3.1-pro");
    await user.type(screen.getByLabelText("API Key"), "volcengine-secret");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(createModel).toHaveBeenCalledWith({
      provider: "volcengine", modelId: "kimi-k3.1-pro", apiKey: "volcengine-secret",
    });
  });

  it("creates a manual stdio MCP from the settings page", async () => {
    const user = userEvent.setup();
    const onCreateMcp = vi.fn().mockResolvedValue(undefined);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props({ onCreateMcp })} />);

    await screen.findByRole("button", { name: "添加模型" });
    await user.click(screen.getByRole("tab", { name: /MCP Servers/ }));
    await user.click(screen.getByRole("button", { name: "添加 MCP" }));
    await user.type(screen.getByLabelText("名称"), "filesystem");
    expect(screen.getByLabelText("工作目录")).toHaveAttribute(
      "placeholder", "留空使用 ~/Eidos/eidos-mcp/filesystem/",
    );
    await user.type(screen.getByLabelText("启动命令"), "npx");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(onCreateMcp).toHaveBeenCalledWith({
      serverId: "filesystem",
      executable: "npx",
      argv: [],
      env: {},
      envNames: [],
      permissionProfile: "workspace_read",
      startupTimeoutSeconds: 15,
      toolTimeoutSeconds: 60,
    });
  });

  it("edits and unloads a manual MCP from its edit dialog", async () => {
    const user = userEvent.setup();
    const onUpdateMcp = vi.fn().mockResolvedValue(undefined);
    const onRemoveMcp = vi.fn().mockResolvedValue(undefined);
    const server: McpServerRecord = {
      schemaVersion: 1,
      pluginId: "manual",
      pluginVersion: "manual",
      pluginHash: "a".repeat(64),
      serverId: "filesystem",
      executable: "npx",
      argv: ["-y", "@modelcontextprotocol/server-everything"],
      envNames: ["PATH"],
      cwd: "/Users/xielei",
      permissionProfile: "connector",
      startupTimeoutSeconds: 15,
      toolTimeoutSeconds: 60,
      declaredEnabled: true,
      consented: false,
      available: false,
      updatedAt: 1,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props({ mcpServers: [server], onUpdateMcp, onRemoveMcp })} />);

    await screen.findByRole("button", { name: "添加模型" });
    await user.click(screen.getByRole("tab", { name: /MCP Servers/ }));
    await user.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.getByRole("heading", { name: "编辑 MCP Server" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("名称")).toHaveFocus());
    expect(screen.getByLabelText("启动命令")).toHaveValue("npx");
    expect(screen.getByLabelText("环境变量名 1")).toHaveValue("PATH");
    const envValInput = screen.getByLabelText("环境变量值 1");
    await user.type(envValInput, "custom-path");
    expect(envValInput).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "显示变量值" }));
    expect(envValInput).toHaveAttribute("type", "text");
    await user.clear(screen.getByLabelText("启动命令"));
    await user.type(screen.getByLabelText("启动命令"), "node");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(onUpdateMcp).toHaveBeenCalledWith(expect.objectContaining({
      serverId: "filesystem",
      executable: "node",
      argv: ["-y", "@modelcontextprotocol/server-everything"],
      envNames: ["PATH"],
      permissionProfile: "connector",
      startupTimeoutSeconds: 15,
      toolTimeoutSeconds: 60,
    }));

    await user.click(screen.getByRole("button", { name: "编辑" }));
    await user.click(screen.getByRole("button", { name: "卸载" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("卸载 MCP Server？");
    await user.click(screen.getByRole("alertdialog").querySelector("button.btn--danger")!);
    expect(onRemoveMcp).toHaveBeenCalledWith("filesystem");
    expect(await screen.findByText("MCP Server “filesystem” 已卸载")).toBeInTheDocument();
  });

  it("toggles technical details with the '其他设置' button in MCP settings", async () => {
    const user = userEvent.setup();
    const server: McpServerRecord = {
      schemaVersion: 1,
      pluginId: "manual",
      pluginVersion: "manual",
      pluginHash: "a".repeat(64),
      serverId: "filesystem",
      executable: "npx",
      argv: ["-y", "@modelcontextprotocol/server-everything"],
      envNames: ["PATH"],
      cwd: "/Users/xielei",
      permissionProfile: "connector",
      startupTimeoutSeconds: 15,
      toolTimeoutSeconds: 60,
      declaredEnabled: true,
      consented: false,
      available: false,
      updatedAt: 1,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
    } as EidosRuntimeAPI;
    const { container } = render(<SettingsPage {...props({ mcpServers: [server] })} />);

    await screen.findByRole("button", { name: "添加模型" });
    await user.click(screen.getByRole("tab", { name: /MCP Servers/ }));

    expect(screen.queryByRole("button", { name: "展开技术参数" })).not.toBeInTheDocument();
    expect(screen.queryByText("启动指令")).not.toBeInTheDocument();
    expect(container.querySelector(".mcp-meta-cmd-preview")).not.toBeInTheDocument();

    const toggleButton = screen.getByRole("button", { name: "其他设置" });
    expect(toggleButton).toHaveAttribute("aria-expanded", "false");

    await user.click(toggleButton);
    expect(toggleButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("启动指令")).toBeInTheDocument();
    expect(screen.getByText("工作目录 (CWD)")).toBeInTheDocument();

    await user.click(toggleButton);
    expect(toggleButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("启动指令")).not.toBeInTheDocument();
  });

  it("deleting a model requires confirmation and can be canceled or confirmed", async () => {
    const user = userEvent.setup();
    const deleteModel = vi.fn().mockResolvedValue(undefined);
    const onModelsChanged = vi.fn().mockResolvedValue(undefined);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
      deleteModel,
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props({ onModelsChanged })} />);

    await screen.findByRole("button", { name: "添加模型" });
    const deleteBtn = screen.getByRole("button", { name: "删除 DeepSeek-V4 Flash" });
    await user.click(deleteBtn);

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent("删除模型 “DeepSeek-V4 Flash”？");
    expect(dialog).toHaveTextContent("确定要删除模型 “DeepSeek-V4 Flash” 吗？删除后该模型将从本地配置中移除，无法在会话中继续使用。");

    // Click cancel
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(deleteModel).not.toHaveBeenCalled();

    // Reopen and confirm
    await user.click(deleteBtn);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.click(screen.getByRole("alertdialog").querySelector("button.btn--danger")!);

    expect(deleteModel).toHaveBeenCalledWith("deepseek-v4-flash");
    expect(onModelsChanged).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("模型已删除")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("shows error inside confirm dialog when model deletion fails", async () => {
    const user = userEvent.setup();
    const deleteModel = vi.fn().mockRejectedValue(new Error("runtime failure"));
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModelPresets: vi.fn().mockResolvedValue(presets),
      deleteModel,
    } as EidosRuntimeAPI;
    render(<SettingsPage {...props()} />);

    await screen.findByRole("button", { name: "添加模型" });
    await user.click(screen.getByRole("button", { name: "删除 DeepSeek-V4 Flash" }));
    await user.click(screen.getByRole("alertdialog").querySelector("button.btn--danger")!);

    expect(deleteModel).toHaveBeenCalledWith("deepseek-v4-flash");
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent("模型删除失败，请查看 Runtime 日志。");
  });
});
