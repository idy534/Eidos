import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI, ModelOption } from "../../contracts.js";
import type { CustomProviderResult } from "../../../../shared/custom-models.js";
import { ModelSettings } from "./ModelSettings.js";

const presets = { providers: [{ id: "deepseek", name: "DeepSeek", models: [{ id: "builtin", name: "内置模型" }] }] };
const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const discovered: CustomProviderResult = { state: "success", message: "已获取 2 个模型", models: [
  { id: "company/reasoner", name: "公司推理模型", contextWindowTokens: 64000, maxOutputTokens: 4096, supportsImages: true,
    reasoning: { defaultSelection: "high", selections: ["low", "high", "xhigh"] } },
  { id: "basic-model" },
] };
function setup(overrides: Partial<EidosRuntimeAPI> = {}, models: ModelOption[] = []) {
  const api = { listModelPresets: vi.fn().mockResolvedValue(presets), createModel: vi.fn().mockResolvedValue({}),
    updateModel: vi.fn().mockResolvedValue({}), discoverCustomModels: vi.fn().mockResolvedValue(discovered), ...overrides };
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: api });
  render(<ModelSettings storageHealthReady modelList={{ models }} onModelsChanged={async () => {}} onShowToast={vi.fn()} />);
  return api;
}
async function customForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "添加模型" }));
  expect(screen.queryByRole("button", { name: "测试连接" })).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText("提供商"), "custom");
  await user.type(screen.getByLabelText("提供商 ID"), "company-gateway");
  await user.type(screen.getByLabelText("接口地址"), "http://localhost:3425/v1");
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

describe("Custom provider settings", () => {
  it.each([false, true])("preserves the draft after clicking outside the model dialog (custom=%s)", async custom => {
    const user = userEvent.setup(); const api = setup();
    if (custom) await customForm(user);
    else {
      await user.click(await screen.findByRole("button", { name: "添加模型" }));
      await waitFor(() => expect(screen.getByLabelText("提供商")).toHaveFocus());
    }
    await user.type(screen.getByLabelText("API Key"), "draft-fixture-key");
    const dialog = screen.getByRole("dialog");
    await user.click(dialog.closest(".modal-backdrop")!);
    expect(dialog).toBeInTheDocument();
    expect(screen.getByLabelText("API Key")).toHaveValue("draft-fixture-key");
    if (custom) {
      expect(screen.getByLabelText("提供商 ID")).toHaveValue("company-gateway");
      expect(screen.getByLabelText("接口地址")).toHaveValue("http://localhost:3425/v1");
      await user.click(screen.getByRole("button", { name: "取消" }));
    } else await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.createModel).not.toHaveBeenCalled();
  });

  it("only tests the custom draft and fills model metadata through the searchable list", async () => {
    const user = userEvent.setup(); const api = setup();
    await customForm(user);
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(api.discoverCustomModels).toHaveBeenCalledWith({ provider: "custom", baseUrl: "http://localhost:3425/v1", apiKey: "" });
    expect(await screen.findByText("已获取 2 个模型")).toBeInTheDocument();
    await user.click(screen.getByRole("combobox", { name: "模型 ID" }));
    await user.type(screen.getByRole("combobox", { name: "模型 ID" }), "reasoner");
    const option = screen.getByRole("option", { name: /company\/reasoner/ });
    expect(option.querySelector("small")).toHaveTextContent("公司推理模型");
    await user.click(option);
    expect(screen.getByLabelText("展示名")).toHaveValue("公司推理模型");
    expect(screen.getByLabelText("上下文窗口")).toHaveValue("64000");
    expect(screen.getByLabelText("最大输出")).toHaveValue("4096");
    expect(screen.getByRole("checkbox", { name: "图像" })).toBeChecked();
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("auto");
    expect(screen.getByRole("checkbox", { name: "超高" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "低", exact: true })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "高", exact: true })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "中", exact: true })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({ provider: "custom", modelId: "company/reasoner", apiKey: "", custom: expect.objectContaining({ name: "公司推理模型", contextWindowTokens: 64000, reasoningControl: "configured", reasoning: { defaultSelection: null, selections: ["low", "high", "xhigh"] } }) }));
  });

  it.each([false, true])("fills image input for a discovered model and preserves manual overrides (editing=%s)", async editing => {
    const custom = { providerId: "company-gateway", providerName: "公司网关", baseUrl: "https://gateway.invalid/v1", name: "已保存模型", contextWindowTokens: 64000, maxOutputTokens: 4096, supportsImages: false };
    const model: ModelOption = { id: "custom-stable", modelId: "image-model", name: custom.name, provider: "custom", vendor: custom.providerName, url: custom.baseUrl + "/chat/completions", custom, supportsToolCall: true, supportsImages: false, supportsReasoning: false };
    const user = userEvent.setup();
    const api = setup({ discoverCustomModels: vi.fn().mockResolvedValue({ state: "success", message: "已获取 3 个模型", models: [
      { id: "image-model", name: "图像模型", contextWindowTokens: 1000000, maxOutputTokens: 128000, supportsImages: true },
      { id: "text-only", name: "文本模型", supportsImages: false },
      { id: "unknown", name: "未知能力" },
    ] }) }, editing ? [model] : []);
    if (editing) await user.click(await screen.findByRole("button", { name: "编辑 已保存模型" }));
    else await customForm(user);
    const choose = async (id: string) => {
      await user.click(screen.getByLabelText("模型 ID"));
      await user.click(screen.getByRole("option", { name: new RegExp(id) }));
    };
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText("已获取 3 个模型");
    await choose("image-model");
    const images = screen.getByRole("checkbox", { name: "图像" });
    expect(images).toBeChecked();
    await user.click(images);
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText("已获取 3 个模型");
    await choose("image-model");
    expect(images).not.toBeChecked();
    await choose("unknown");
    expect(images).not.toBeChecked();
    await choose("image-model");
    expect(images).toBeChecked();
    await choose("text-only");
    expect(images).not.toBeChecked();
    await choose("image-model");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(editing ? api.updateModel : api.createModel).toHaveBeenCalledWith(expect.objectContaining({ modelId: "image-model", custom: expect.objectContaining({ supportsImages: true }) }));
  });

  it("fills a different model and reports missing metadata", async () => {
    const user = userEvent.setup(); setup(); await customForm(user);
    await user.type(screen.getByLabelText("展示名"), "我的名称");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText("已获取 2 个模型");
    await user.click(screen.getByRole("combobox", { name: "模型 ID" }));
    await user.click(screen.getByRole("option", { name: /basic-model/ }));
    expect(screen.getByLabelText("展示名")).toHaveValue("basic-model");
    expect(screen.getByLabelText("上下文窗口")).toHaveValue("");
    expect(screen.getByText(/接口未提供上下文窗口、最大输出/)).toBeInTheDocument();
  });

  it("fills supported strengths without a declared default and refills when switching models", async () => {
    const user = userEvent.setup(); setup({ discoverCustomModels: vi.fn().mockResolvedValue({ state: "success", message: "已获取 2 个模型", models: [
      { id: "first", name: "First Model", contextWindowTokens: 127001, maxOutputTokens: 8192, reasoningSelections: ["low", "high", "xhigh"] },
      { id: "second", name: "Second Model", contextWindowTokens: 262144, maxOutputTokens: 32768, reasoning: { selections: ["medium", "max"], defaultSelection: "max" } },
    ] }) });
    await customForm(user); await user.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText("已获取 2 个模型");
    await user.click(screen.getByLabelText("模型 ID"));
    await user.click(screen.getByRole("option", { name: /First Model/ }));
    expect(screen.getByLabelText("上下文窗口")).toHaveValue("127001");
    expect(screen.getByRole("checkbox", { name: "超高" })).toBeChecked();
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("auto");
    expect(screen.queryByText(/当前使用首个档位/)).not.toBeInTheDocument();
    await user.clear(screen.getByLabelText("展示名"));
    await user.type(screen.getByLabelText("展示名"), "我的名称");
    await user.selectOptions(screen.getByLabelText("默认思考强度"), "high");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText("已获取 2 个模型");
    await user.click(screen.getByLabelText("模型 ID"));
    await user.click(screen.getByRole("option", { name: /First Model/ }));
    expect(screen.getByLabelText("展示名")).toHaveValue("我的名称");
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("high");
    await user.click(screen.getByLabelText("模型 ID"));
    await user.click(screen.getByRole("option", { name: /Second Model/ }));
    expect(screen.getByLabelText("展示名")).toHaveValue("Second Model");
    expect(screen.getByLabelText("上下文窗口")).toHaveValue("262144");
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("auto");
    expect(screen.getByRole("checkbox", { name: "中", exact: true })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "最高" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "超高" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "低", exact: true })).not.toBeChecked();
  });

  it("uses required stars and an input icon to show and hide the API key", async () => {
    const user = userEvent.setup(); setup(); await customForm(user);
    expect(screen.queryByText("必填", { exact: true })).not.toBeInTheDocument();
    for (const field of ["提供商 ID", "提供商名称", "接口地址", "模型 ID", "展示名"]) {
      expect(screen.getByLabelText(field)).toHaveAttribute("aria-required", "true");
    }
    for (const field of ["上下文窗口", "最大输出", "API Key"]) {
      expect(screen.getByLabelText(field)).not.toHaveAttribute("aria-required", "true");
    }
    expect(screen.getAllByText("*", { exact: true })).toHaveLength(5);
    expect(screen.getByLabelText("API Key")).not.toHaveAttribute("aria-required", "true");
    await user.type(screen.getByLabelText("API Key"), "fixture-key");
    const toggle = screen.getByRole("button", { name: "显示 API Key" });
    expect(toggle.textContent).toBe("");
    expect(toggle.querySelector("svg")).toBeInTheDocument();
    expect(toggle.parentElement).toContainElement(screen.getByLabelText("API Key"));
    await user.click(toggle);
    expect(screen.getByLabelText("API Key")).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "隐藏 API Key" }));
    expect(screen.getByLabelText("API Key")).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    const status = await screen.findByText("已获取 2 个模型");
    expect(status.parentElement).toContainElement(screen.getByRole("button", { name: "测试连接" }));
  });

  it("supports a keyless manual model even when listing is unavailable", async () => {
    const user = userEvent.setup(); const api = setup({ discoverCustomModels: vi.fn().mockResolvedValue({ state: "unavailable", message: "此接口未提供模型列表，你可以手动填写模型 ID。", models: [] }) });
    await customForm(user);
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText(/此接口未提供模型列表/);
    await user.type(screen.getByRole("combobox", { name: "模型 ID" }), "private-model");
    await user.type(screen.getByLabelText("展示名"), "本地模型");
    await user.type(screen.getByLabelText("上下文窗口"), "32");
    await user.type(screen.getByLabelText("最大输出"), "2");
    await user.selectOptions(screen.getByLabelText("默认思考强度"), "none");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({ modelId: "private-model", apiKey: "", custom: expect.objectContaining({ contextWindowTokens: 32, maxOutputTokens: 2, reasoning: { selections: ["none"], defaultSelection: "none" } }) }));
  });

  it("discards stale listing results after the address changes", async () => {
    let resolve!: (value: CustomProviderResult) => void;
    const user = userEvent.setup(); setup({ discoverCustomModels: vi.fn().mockImplementation(() => new Promise(done => { resolve = done; })) });
    await customForm(user); await user.click(screen.getByRole("button", { name: "测试连接" }));
    await user.clear(screen.getByLabelText("接口地址"));
    await user.type(screen.getByLabelText("接口地址"), "https://new.invalid/v1");
    resolve(discovered);
    await waitFor(() => expect(screen.queryByText("已获取 2 个模型")).not.toBeInTheDocument());
    await user.click(screen.getByRole("combobox", { name: "模型 ID" }));
    expect(screen.queryByRole("option", { name: /company\/reasoner/ })).not.toBeInTheDocument();
  });

  it("uses stored credentials only when editing and clears a key explicitly", async () => {
    const custom = { providerId: "company-gateway", providerName: "公司网关", baseUrl: "https://gateway.invalid/v1", name: "已保存模型", contextWindowTokens: 64000, maxOutputTokens: 4096 };
    const model = { id: "custom-stable", modelId: "remote-id", name: custom.name, provider: "custom", vendor: custom.providerName, url: custom.baseUrl + "/chat/completions", custom, supportsToolCall: true, supportsImages: false, supportsReasoning: false } satisfies ModelOption;
    const user = userEvent.setup(); const api = setup({}, [model]);
    await user.click(await screen.findByRole("button", { name: "编辑 已保存模型" }));
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(api.discoverCustomModels).toHaveBeenCalledWith({ provider: "custom", baseUrl: custom.baseUrl, configId: model.id });
    await screen.findByText("已获取 2 个模型");
    await user.type(screen.getByLabelText("API Key"), "new-key");
    await user.clear(screen.getByLabelText("API Key"));
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.updateModel).toHaveBeenCalledWith(expect.objectContaining({ id: model.id, modelId: "remote-id", apiKey: "" }));
  });

  it("normalizes a pasted full endpoint and uses keyboard model selection", async () => {
    const user = userEvent.setup(); const api = setup(); await customForm(user);
    await user.type(screen.getByLabelText("接口地址"), "/responses");
    expect(screen.getByLabelText("接口地址")).toHaveValue("http://localhost:3425/v1/responses");
    await user.tab();
    expect(screen.getByLabelText("接口地址")).toHaveValue("http://localhost:3425/v1");
    expect(screen.getByLabelText("接口协议")).toHaveValue("openai_responses");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    await screen.findByText("已获取 2 个模型");
    await user.click(screen.getByRole("combobox", { name: "模型 ID" }));
    await user.keyboard("{Enter}");
    expect(screen.getByLabelText("展示名")).toHaveValue("公司推理模型");
    expect(api.discoverCustomModels).toHaveBeenCalledTimes(1);
  });

  it("follows the provider ID until its name is edited and removes redundant controls", async () => {
    const user = userEvent.setup(); setup(); await customForm(user);
    expect(screen.getByLabelText("提供商名称")).toHaveValue("company-gateway");
    await user.type(screen.getByLabelText("提供商 ID"), "-2");
    expect(screen.getByLabelText("提供商名称")).toHaveValue("company-gateway-2");
    await user.clear(screen.getByLabelText("提供商名称"));
    await user.type(screen.getByLabelText("提供商名称"), "公司接口");
    await user.type(screen.getByLabelText("提供商 ID"), "-3");
    expect(screen.getByLabelText("提供商名称")).toHaveValue("公司接口");
    expect(screen.getByLabelText("API Key")).not.toHaveAttribute("placeholder", "无鉴权接口可以留空");
    for (const copy of ["无需 API Key", "选填", "高级兼容设置", "思考控制", "思考参数格式", "只发送你声明的档位", "测试只获取模型列表，不生成回复。", "Agent 执行任务需要此能力"]) {
      expect(screen.queryByText(copy)).not.toBeInTheDocument();
    }
  });

  it("starts with an empty provider name and validates provider identity and custom token input", async () => {
    const user = userEvent.setup(); const api = setup();
    await user.click(await screen.findByRole("button", { name: "添加模型" }));
    await user.selectOptions(screen.getByLabelText("提供商"), "custom");
    expect(screen.getByLabelText("提供商名称")).toHaveValue("");
    expect(screen.getByLabelText("API Key")).toHaveAttribute("placeholder", "");
    await user.type(screen.getByLabelText("提供商 ID"), "bad id");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请填写提供商 ID");
    await user.clear(screen.getByLabelText("提供商 ID"));
    await user.type(screen.getByLabelText("提供商 ID"), "valid-id");
    await user.type(screen.getByLabelText("接口地址"), "https://gateway.invalid/v1");
    await user.type(screen.getByLabelText("模型 ID"), "manual");
    await user.type(screen.getByLabelText("展示名"), "我的模型");
    await user.type(screen.getByLabelText("上下文窗口"), "invalid");
    await user.type(screen.getByLabelText("最大输出"), "32K");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("有效的 Token 上限");
    expect(api.createModel).not.toHaveBeenCalled();
  });

  it("reveals a saved key and sends the key explicitly after editing", async () => {
    const custom = { providerId: "company-gateway", providerName: "公司网关", baseUrl: "https://gateway.invalid/v1", name: "已保存模型", contextWindowTokens: 64000, maxOutputTokens: 4096 };
    const model = { id: "custom-stable", modelId: "remote-id", name: custom.name, provider: "custom", vendor: custom.providerName, url: custom.baseUrl + "/chat/completions", custom, supportsToolCall: true, supportsImages: false, supportsReasoning: false, apiKey: "saved-key" } satisfies ModelOption;
    const user = userEvent.setup(); const api = setup({}, [model]);
    await user.click(await screen.findByRole("button", { name: "编辑 已保存模型" }));
    const keyInput = screen.getByLabelText("API Key");
    expect(keyInput).toHaveValue("saved-key");
    expect(keyInput).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "显示 API Key" }));
    expect(keyInput).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.updateModel).toHaveBeenCalledTimes(1));
    expect(api.updateModel.mock.calls[0]![0]).toHaveProperty("apiKey", "saved-key");
    await user.click(screen.getByRole("button", { name: "编辑 已保存模型" }));
    await user.clear(screen.getByLabelText("接口地址"));
    await user.type(screen.getByLabelText("接口地址"), "https://other.invalid/v1");
    await user.click(screen.getByRole("button", { name: "测试连接" }));
    expect(api.discoverCustomModels).toHaveBeenCalledWith({ provider: "custom", configId: model.id, baseUrl: "https://other.invalid/v1", apiKey: "saved-key" });
  });

  it("supports token presets, keyboard selection and editable custom limits", async () => {
    const user = userEvent.setup(); const api = setup(); await customForm(user);
    await user.click(screen.getByLabelText("上下文窗口"));
    expect(screen.getByRole("option", { name: /256K/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /512K/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /1024K/ })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /512K/ }));
    await user.click(screen.getByLabelText("最大输出"));
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(screen.getByLabelText("上下文窗口")).toHaveValue("524288");
    expect(screen.getByLabelText("最大输出")).toHaveValue("65536");
    await user.clear(screen.getByLabelText("上下文窗口"));
    await user.type(screen.getByLabelText("上下文窗口"), "1000000");
    await user.tab();
    expect(screen.getByLabelText("上下文窗口")).toHaveValue("1000000");
    await user.type(screen.getByLabelText("模型 ID"), "manual");
    await user.type(screen.getByLabelText("展示名"), "我的模型");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({ custom: expect.objectContaining({ providerId: "company-gateway", providerName: "company-gateway", contextWindowTokens: 1000000, maxOutputTokens: 65536 }) }));
  });

  it("marks only the committed preset and commits custom values without replacing them", async () => {
    const user = userEvent.setup(); setup(); await customForm(user);
    const field = screen.getByLabelText("上下文窗口");
    await user.click(field);
    await user.click(screen.getByRole("option", { name: "256K" }));
    expect(field).toHaveValue("262144");
    await user.click(field);
    const selected = screen.getByRole("option", { name: "256K" });
    const hovered = screen.getByRole("option", { name: "512K" });
    await user.hover(hovered);
    expect(within(screen.getByRole("listbox", { name: "上下文窗口预设" })).getAllByRole("option", { selected: true })).toEqual([selected]);
    expect(selected.querySelector(".custom-model-token-check")).toBeInTheDocument();
    expect(hovered).toHaveAttribute("aria-selected", "false");
    expect(hovered).toHaveClass("is-active");
    expect(selected).not.toHaveClass("is-active");
    await user.clear(field);
    await user.type(field, "123.5K");
    await user.keyboard("{Enter}");
    expect(field).toHaveValue("126464");
    expect(screen.queryByRole("listbox", { name: "上下文窗口预设" })).not.toBeInTheDocument();
    await user.click(field);
    expect(within(screen.getByRole("listbox", { name: "上下文窗口预设" })).queryByRole("option", { selected: true })).not.toBeInTheDocument();
  });

  it.each([false, true])("keeps the connection button stable and delays progress (slow=%s)", async slow => {
    let resolve!: (value: CustomProviderResult) => void;
    const user = userEvent.setup(); setup({ discoverCustomModels: vi.fn().mockImplementation(() => new Promise(done => { resolve = done; })) });
    await customForm(user);
    vi.useFakeTimers();
    const button = screen.getByRole("button", { name: "测试连接" });
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("测试连接");
    expect(button.querySelector("svg")).not.toBeInTheDocument();
    expect(screen.queryByText("正在获取模型…")).not.toBeInTheDocument();
    if (slow) {
      await act(async () => { vi.advanceTimersByTime(250); });
      expect(screen.getByText("正在获取模型…")).toBeInTheDocument();
      expect(button.querySelector("svg")).not.toBeInTheDocument();
    }
    await act(async () => { resolve(discovered); });
    await act(async () => { vi.advanceTimersByTime(250); });
    expect(screen.queryByText("正在获取模型…")).not.toBeInTheDocument();
    expect(screen.getByText("已获取 2 个模型")).toBeInTheDocument();
    expect(button).toBeEnabled();
    expect(button.querySelector("svg")).not.toBeInTheDocument();
  });

  it("keeps a valid default when reasoning strengths are checked or removed", async () => {
    const user = userEvent.setup(); const api = setup(); await customForm(user);
    await user.click(screen.getByRole("checkbox", { name: "最高" }));
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("auto");
    await user.selectOptions(screen.getByLabelText("默认思考强度"), "high");
    expect(screen.getByRole("checkbox", { name: "高", exact: true })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "高", exact: true }));
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("none");
    await user.type(screen.getByLabelText("模型 ID"), "manual");
    await user.type(screen.getByLabelText("展示名"), "我的模型");
    await user.type(screen.getByLabelText("上下文窗口"), "256K");
    await user.type(screen.getByLabelText("最大输出"), "32K");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({ custom: expect.objectContaining({ reasoning: { selections: ["none", "max"], defaultSelection: "none" }, reasoningFormat: "openai" }) }));
  });

  it("keeps supported strengths when returning to automatic and editing a saved model", async () => {
    const custom = { providerId: "company-gateway", providerName: "公司网关", baseUrl: "https://gateway.invalid/v1", name: "已保存模型", contextWindowTokens: 64000, maxOutputTokens: 4096,
      reasoningControl: "configured", reasoning: { selections: ["low", "high", "max"] } } as const;
    const model = { id: "custom-auto", modelId: "reasoner", name: custom.name, provider: "custom", vendor: custom.providerName, url: custom.baseUrl + "/chat/completions", custom: { ...custom, reasoning: { ...custom.reasoning, selections: [...custom.reasoning.selections] } }, supportsToolCall: true, supportsImages: false, supportsReasoning: true } satisfies ModelOption;
    const user = userEvent.setup(); const api = setup({}, [model]);
    await user.click(await screen.findByRole("button", { name: "编辑 已保存模型" }));
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("auto");
    expect(screen.getByRole("checkbox", { name: "最高" })).toBeChecked();
    await user.selectOptions(screen.getByLabelText("默认思考强度"), "high");
    await user.selectOptions(screen.getByLabelText("默认思考强度"), "auto");
    for (const name of ["低", "高", "最高"]) expect(screen.getByRole("checkbox", { name, exact: true })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "中", exact: true }));
    expect(screen.getByLabelText("默认思考强度")).toHaveValue("auto");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.updateModel).toHaveBeenCalledWith(expect.objectContaining({ id: "custom-auto", custom: expect.objectContaining({ reasoning: { defaultSelection: null, selections: ["none", "low", "high", "max", "medium"] } }) }));
  });

  it("uses default values for context window and max output when left empty", async () => {
    const user = userEvent.setup(); const api = setup(); await customForm(user);
    await user.type(screen.getByRole("combobox", { name: "模型 ID" }), "default-limits-model");
    await user.type(screen.getByLabelText("展示名"), "默认限制模型");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({
      modelId: "default-limits-model",
      custom: expect.objectContaining({
        contextWindowTokens: 128000,
        maxOutputTokens: 8192,
      }),
    }));
  });

  it("supports copying an existing custom model prefilling only provider settings", async () => {
    const custom = {
      providerId: "local-provider",
      providerName: "Local Gateway",
      baseUrl: "https://gateway.invalid/v1",
      name: "已保存自定义模型",
      contextWindowTokens: 64000,
      maxOutputTokens: 4096,
      wireApi: "chat_completions" as const,
    };
    const model = {
      id: "custom-local",
      modelId: "local-m1",
      name: custom.name,
      provider: "custom",
      vendor: custom.providerName,
      url: custom.baseUrl + "/chat/completions",
      custom,
      supportsToolCall: true,
      supportsImages: false,
      supportsReasoning: false,
    } satisfies ModelOption;
    const user = userEvent.setup();
    const api = setup({}, [model]);
    const copyButton = await screen.findByRole("button", { name: "复制 已保存自定义模型" });
    expect(copyButton).toBeInTheDocument();
    await user.click(copyButton);

    expect(screen.getByRole("heading", { name: "添加模型" })).toBeInTheDocument();
    expect(screen.getByLabelText("提供商 ID")).toHaveValue("local-provider");
    expect(screen.getByLabelText("提供商名称")).toHaveValue("Local Gateway");
    expect(screen.getByLabelText("接口地址")).toHaveValue("https://gateway.invalid/v1");
    expect(screen.getByLabelText("接口协议")).toHaveValue("chat_completions");
    expect(screen.getByLabelText("API Key")).toHaveValue("");

    expect(screen.getByRole("combobox", { name: "模型 ID" })).toHaveValue("");
    expect(screen.getByLabelText("展示名")).toHaveValue("");
    expect(screen.getByLabelText("上下文窗口")).toHaveValue("");
    expect(screen.getByLabelText("最大输出")).toHaveValue("");
  });

  it("does not show copy button for built-in preset models", async () => {
    const builtinModel = {
      id: "deepseek-chat",
      name: "DeepSeek Chat",
      vendor: "深度求索",
      provider: "deepseek",
      url: "https://api.seek.com/chat/completions",
      supportsToolCall: true,
      supportsImages: false,
      supportsReasoning: false,
    } satisfies ModelOption;
    setup({}, [builtinModel]);
    expect(await screen.findByRole("button", { name: "编辑 DeepSeek Chat" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制 DeepSeek Chat" })).not.toBeInTheDocument();
  });

  it("validates that context window must be greater than max output when creating a custom model", async () => {
    const user = userEvent.setup();
    const api = setup();
    await customForm(user);
    await user.type(screen.getByRole("combobox", { name: "模型 ID" }), "test-model");
    await user.type(screen.getByLabelText("展示名"), "测试模型");

    // Equal (4096 == 4096)
    await user.type(screen.getByLabelText("上下文窗口"), "4096");
    await user.type(screen.getByLabelText("最大输出"), "4096");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("上下文窗口必须大于最大输出。");
    expect(api.createModel).not.toHaveBeenCalled();

    // Less than (2048 < 4096)
    await user.clear(screen.getByLabelText("上下文窗口"));
    await user.type(screen.getByLabelText("上下文窗口"), "2048");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("上下文窗口必须大于最大输出。");
    expect(api.createModel).not.toHaveBeenCalled();

    // Greater than (8192 > 4096) -> succeeds
    await user.clear(screen.getByLabelText("上下文窗口"));
    await user.type(screen.getByLabelText("上下文窗口"), "8192");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.createModel).toHaveBeenCalledWith(
      expect.objectContaining({
        modelId: "test-model",
        custom: expect.objectContaining({
          contextWindowTokens: 8192,
          maxOutputTokens: 4096,
        }),
      }),
    );
  });

  it("validates that context window must be greater than max output when editing a custom model", async () => {
    const custom = {
      providerId: "company-gateway",
      providerName: "公司网关",
      baseUrl: "https://gateway.invalid/v1",
      name: "已保存模型",
      contextWindowTokens: 64000,
      maxOutputTokens: 4096,
    };
    const model = {
      id: "custom-stable",
      modelId: "remote-id",
      name: custom.name,
      provider: "custom",
      vendor: custom.providerName,
      url: custom.baseUrl + "/chat/completions",
      custom,
      supportsToolCall: true,
      supportsImages: false,
      supportsReasoning: false,
    } satisfies ModelOption;
    const user = userEvent.setup();
    const api = setup({}, [model]);

    await user.click(await screen.findByRole("button", { name: "编辑 已保存模型" }));

    // Equal (4096 == 4096)
    await user.clear(screen.getByLabelText("上下文窗口"));
    await user.type(screen.getByLabelText("上下文窗口"), "4096");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("上下文窗口必须大于最大输出。");
    expect(api.updateModel).not.toHaveBeenCalled();

    // Less than (2048 < 4096)
    await user.clear(screen.getByLabelText("上下文窗口"));
    await user.type(screen.getByLabelText("上下文窗口"), "2048");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("上下文窗口必须大于最大输出。");
    expect(api.updateModel).not.toHaveBeenCalled();

    // Greater than (32000 > 4096) -> succeeds
    await user.clear(screen.getByLabelText("上下文窗口"));
    await user.type(screen.getByLabelText("上下文窗口"), "32000");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.updateModel).toHaveBeenCalledWith(
      expect.objectContaining({
        id: model.id,
        custom: expect.objectContaining({
          contextWindowTokens: 32000,
          maxOutputTokens: 4096,
        }),
      }),
    );
  });
});
