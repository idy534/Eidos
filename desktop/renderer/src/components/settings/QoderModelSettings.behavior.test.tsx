import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI, ModelListResult } from "../../contracts.js";
import { ModelSettings } from "./ModelSettings.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const ready = { resourceReady: true, phase: "ready" as const, models: [{ id: "efficient", name: "Efficient", enabled: true }] };
function setup(state: import("../../../../shared/qoder.js").QoderConnectionState = ready, modelList: ModelListResult = { models: [] }) {
  const api = { listModelPresets: vi.fn().mockResolvedValue({ providers: [{ id: "deepseek", name: "DeepSeek", models: [{ id: "builtin", name: "内置模型" }] }] }),
    qoderConnection: vi.fn().mockResolvedValue(state), retryResource: vi.fn().mockResolvedValue({ accepted: true }),
    createModel: vi.fn().mockResolvedValue({}), updateModel: vi.fn().mockResolvedValue({}) };
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: api as unknown as EidosRuntimeAPI });
  const onModelsChanged = vi.fn().mockResolvedValue(undefined);
  const props = { storageHealthReady: true, modelList, onModelsChanged, onShowToast: vi.fn() };
  const view = render(<ModelSettings {...props} />);
  return { ...api, onModelsChanged, updateModels: (next: ModelListResult) => view.rerender(<ModelSettings {...props} modelList={next} />) };
}
async function open(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "添加模型" }));
  await user.selectOptions(screen.getByLabelText("提供商"), "qoder");
}
afterEach(() => {
  vi.restoreAllMocks();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

describe("Qoder model settings", () => {
  it("saves the account model without a URL or key and uses the actual catalog ID", async () => {
    const user = userEvent.setup(); const api = setup(); await open(user);
    await waitFor(() => expect(screen.getByLabelText("模型名称")).toHaveValue("efficient"));
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("接口地址")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(api.createModel).toHaveBeenCalledWith({ provider: "qoder", modelId: "efficient", apiKey: "",
      qoder: expect.objectContaining({ accountRef: "default", contextWindowTokens: 128000, maxOutputTokens: 8192 }) });
  });
  it("starts browser login only on user action and shows the waiting state", async () => {
    const user = userEvent.setup(); const api = setup(); await open(user);
    expect(api.qoderConnection).toHaveBeenCalledWith({ action: "status" });
    expect(api.qoderConnection).not.toHaveBeenCalledWith({ action: "login" });
    api.qoderConnection.mockResolvedValue({ ...ready, phase: "logging_in" } as unknown as typeof ready);
    await user.click(await screen.findByRole("button", { name: "重新登录" }));
    expect(api.qoderConnection).toHaveBeenCalledWith({ action: "login" });
    expect(await screen.findByText("请在系统浏览器中完成 Qoder 登录。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新模型目录" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("shows missing resources and preserves the draft after a preparation error", async () => {
    const user = userEvent.setup(); const api = setup({ ...ready, resourceReady: false }); await open(user);
    api.retryResource.mockResolvedValue({ accepted: false });
    await user.click(await screen.findByRole("button", { name: "准备模型资源" }));
    expect(api.retryResource).toHaveBeenCalledWith({ packId: "qoder" });
    expect(await screen.findByRole("alert")).toHaveTextContent("当前构建没有 Qoder 资源索引");
    expect(screen.getByLabelText("提供商")).toHaveValue("qoder");
    expect(screen.getByRole("button", { name: "浏览器登录" })).toBeDisabled();
  });
});

it("checks saved login on entry and shows the account as logged in without opening a browser", async () => {
  const user = userEvent.setup();
  const api = setup();
  let complete!: (state: typeof ready) => void;
  const pending = new Promise<typeof ready>(resolve => { complete = resolve; });
  let status: import("../../../../shared/qoder.js").QoderConnectionState = { resourceReady: true, phase: "idle" };
  api.qoderConnection.mockImplementation(({ action }: { action: string }) => action === "check" ? pending : Promise.resolve(status));
  await open(user);
  expect(await screen.findByText("正在检查本地登录状态…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "浏览器登录" })).toBeDisabled();
  expect(api.qoderConnection).toHaveBeenCalledWith({ action: "check" });
  status = ready;
  complete(ready);
  expect(await screen.findByText("已登录")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新登录" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "浏览器登录" })).not.toBeInTheDocument();
  expect(api.qoderConnection).not.toHaveBeenCalledWith({ action: "login" });
  await new Promise(resolve => setTimeout(resolve, 1600));
  expect(api.qoderConnection.mock.calls.filter(([request]) => request.action === "check")).toHaveLength(1);
  await user.selectOptions(screen.getByLabelText("提供商"), "deepseek");
  await user.selectOptions(screen.getByLabelText("提供商"), "qoder");
  await waitFor(() => expect(api.qoderConnection.mock.calls.filter(([request]) => request.action === "check")).toHaveLength(2));
});

it.each([
  [{ resourceReady: true, phase: "idle" as const }, "未登录"],
  [{ resourceReady: true, phase: "error" as const, errorCode: "authentication_failed" }, "登录已失效"],
  [{ resourceReady: true, phase: "error" as const, errorCode: "provider_timeout" }, "连接异常"],
])("keeps missing, expired and unavailable accounts distinct after checking %j", async (state, label) => {
  const user = userEvent.setup();
  const api = setup(state);
  await open(user);
  expect(await screen.findByText(label)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "浏览器登录" })).toBeEnabled();
  expect(api.qoderConnection).toHaveBeenCalledWith({ action: "check" });
  expect(api.qoderConnection).not.toHaveBeenCalledWith({ action: "login" });
  if (state.errorCode === "provider_timeout") expect(screen.queryByText("未登录")).not.toBeInTheDocument();
});

it("checks login when resource preparation completes", async () => {
  const user = userEvent.setup();
  const api = setup({ resourceReady: false });
  await open(user);
  expect(api.qoderConnection).not.toHaveBeenCalledWith({ action: "check" });
  api.qoderConnection.mockResolvedValue(ready);
  expect(await screen.findByText("已登录", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(api.qoderConnection).toHaveBeenCalledWith({ action: "check" });
});

it("ignores a late login check after switching away from Qoder", async () => {
  const user = userEvent.setup();
  const api = setup();
  let complete!: (state: typeof ready) => void;
  const pending = new Promise<typeof ready>(resolve => { complete = resolve; });
  api.qoderConnection.mockImplementation(({ action }: { action: string }) => action === "check" ? pending : Promise.resolve({ resourceReady: true, phase: "idle" }));
  await open(user);
  await screen.findByText("正在检查本地登录状态…");
  await user.selectOptions(screen.getByLabelText("提供商"), "deepseek");
  complete(ready);
  await waitFor(() => expect(screen.getByLabelText("模型名称")).toHaveValue("builtin"));
  expect(screen.queryByText("已登录")).not.toBeInTheDocument();
  expect(api.onModelsChanged).not.toHaveBeenCalled();
});

it.each([
  ["上下文窗口", "100000001", "Qoder 上下文窗口必须在 8193～100000000 Tokens 之间。"],
  ["最大输出", "131073", "Qoder 最大输出必须在 1～131072 Tokens 之间。"],
])("blocks unsupported %s values before sending the model request", async (label, value, message) => {
  const user = userEvent.setup(); const api = setup(); await open(user);
  await waitFor(() => expect(screen.getByLabelText("模型名称")).toHaveValue("efficient"));
  const field = screen.getByLabelText(label);
  await user.clear(field); await user.type(field, value);
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(api.createModel).not.toHaveBeenCalled();
});

it("offers supported presets and saves their actual values", async () => {
  const user = userEvent.setup(); const api = setup(); await open(user);
  await waitFor(() => expect(screen.getByLabelText("模型名称")).toHaveValue("efficient"));
  await user.click(screen.getByLabelText("上下文窗口"));
  expect(screen.queryByRole("option", { name: "256K" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("option", { name: "128K" }));
  await user.click(screen.getByLabelText("最大输出"));
  expect(screen.queryByRole("option", { name: "64K" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("option", { name: "32K" }));
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({
    qoder: expect.objectContaining({ accountRef: "default", contextWindowTokens: 131072, maxOutputTokens: 32768 }),
  }));
});

it("fills actual model metadata and reasoning choices when selecting a model", async () => {
  const user = userEvent.setup();
  const api = setup({ resourceReady: true, phase: "ready", models: [
    { id: "efficient", name: "Efficient", enabled: true, contextWindows: [200000, 400000, 1000000], defaultContextWindow: 200000,
      maxOutputTokens: 32000, supportsImages: true, reasoning: { selections: ["low", "high", "max"], defaultSelection: "high" } },
    { id: "text", name: "Text", enabled: true, defaultContextWindow: 64000, contextWindows: [64000], maxOutputTokens: 4096, supportsImages: false },
  ] });
  await open(user);
  await waitFor(() => expect(screen.getByLabelText("上下文窗口")).toHaveValue("200000"));
  expect(screen.getByLabelText("最大输出")).toHaveValue("32000");
  expect(screen.getByLabelText("思考强度")).toHaveValue("high");
  expect(screen.getByRole("checkbox", { name: "图像" })).toBeChecked();
  await user.click(screen.getByLabelText("上下文窗口"));
  expect(screen.getAllByRole("option").filter(option => option.closest('[role="listbox"]')).map(option => option.textContent)).toEqual(["200K", "400K", "1000K"]);
  await user.click(screen.getByRole("option", { name: "400K" }));
  expect(screen.getByLabelText("上下文窗口")).toHaveValue("400000");
  await user.click(screen.getByLabelText("最大输出"));
  expect(screen.getAllByRole("option").filter(option => option.closest('[role="listbox"]')).map(option => option.textContent)).toEqual(["8K", "16K", "32K"]);
  await user.click(screen.getByRole("option", { name: "16K" }));
  expect(screen.getByLabelText("最大输出")).toHaveValue("16384");
  await user.click(screen.getByLabelText("最大输出"));
  await user.click(screen.getByRole("option", { name: "32K" }));
  expect(screen.getByLabelText("最大输出")).toHaveValue("32000");
  await user.selectOptions(screen.getByLabelText("思考强度"), "max");
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({ qoder: expect.objectContaining({ supportsImages: true,
    contextWindowTokens: 400000, maxOutputTokens: 32000, contextWindowLimit: 1000000, outputTokenLimit: 32000,
    reasoning: { selections: ["low", "high", "max"], defaultSelection: "max" } }) }));
});

it("shares editable input types and resets them from the selected model", async () => {
  const user = userEvent.setup();
  const api = setup({ ...ready, models: [
    { id: "dfmodel", name: "DeepSeek-Flash", enabled: true, supportsImages: false },
    { id: "kmodel_latest", name: "Kimi-K3.1-Pro", enabled: true, supportsImages: true },
  ] });
  await open(user);
  await waitFor(() => expect(screen.getByLabelText("模型名称")).toHaveValue("dfmodel"));
  expect(screen.getByRole("checkbox", { name: "文本" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "文本" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "图像" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "支持工具调用" })).toBeChecked();
  await user.click(screen.getByRole("checkbox", { name: "支持工具调用" }));
  await user.selectOptions(screen.getByLabelText("模型名称"), "kmodel_latest");
  expect(screen.getByRole("checkbox", { name: "图像" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "支持工具调用" })).toBeChecked();
  await user.click(screen.getByRole("checkbox", { name: "图像" }));
  await user.click(screen.getByRole("checkbox", { name: "支持工具调用" }));
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({ modelId: "kmodel_latest",
    qoder: expect.objectContaining({ supportsImages: false, supportsToolCall: false }) }));
});

it("reloads saved labels after catalog refresh without replacing the edit draft", async () => {
  const user = userEvent.setup();
  const old = { id: "qoder-saved", modelId: "dfmodel", name: "dfmodel", vendor: "Qoder", provider: "qoder" as const,
    supportsToolCall: true, supportsImages: false, supportsReasoning: false, qoder: { contextWindowTokens: 64000, maxOutputTokens: 4096 } };
  const api = setup({ ...ready, phase: "idle" }, { models: [old] });
  api.onModelsChanged.mockImplementation(async () => { api.updateModels({ models: [{ ...old, name: "DeepSeek-Flash" }] }); });
  await user.click(screen.getByRole("button", { name: "编辑 dfmodel" }));
  api.qoderConnection.mockResolvedValue({ ...ready, models: [{ id: "dfmodel", name: "DeepSeek-Flash", enabled: true }] });
  await user.click(await screen.findByRole("button", { name: "刷新模型目录" }));
  expect(api.onModelsChanged).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("上下文窗口")).toHaveValue("64000");
  expect(screen.getByLabelText("最大输出")).toHaveValue("4096");
  await user.click(screen.getByRole("button", { name: "取消" }));
  expect(screen.getByRole("button", { name: "编辑 DeepSeek-Flash" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "编辑 dfmodel" })).not.toBeInTheDocument();
});

it("saves provider-default reasoning as null instead of an invalid empty effort", async () => {
  const user = userEvent.setup(); const api = setup({ ...ready, models: [{ ...ready.models[0]!, reasoning: { selections: ["low", "high"], defaultSelection: "high" } }] });
  await open(user);
  await waitFor(() => expect(screen.getByLabelText("思考强度")).toHaveValue("high"));
  await user.selectOptions(screen.getByLabelText("思考强度"), "");
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect(api.createModel).toHaveBeenCalledWith(expect.objectContaining({ qoder: expect.objectContaining({ reasoning: { selections: ["low", "high"], defaultSelection: null } }) }));
});
