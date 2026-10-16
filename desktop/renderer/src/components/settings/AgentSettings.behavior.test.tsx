import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { AgentDefinition, AgentDefinitionDocument } from "../../../../shared/collaboration.generated.js";
import { AgentEditorDialog } from "./AgentEditorDialog.js";
import { AgentSettings } from "./AgentSettings.js";

const definition: AgentDefinition = { schemaVersion: 1, name: "luna-work", description: "实现与验证", instructions: "保留已有修改。", enabled: true, baseRole: "worker" };
const document: AgentDefinitionDocument = { source: "/data/agents/manual-name.toml", version: "a".repeat(64), definition, text: 'name = "luna-work"' };
const model = (provider: string, id: string, selections: string[]) => ({ id, provider, name: id, vendor: provider, url: "https://example.com", supportsToolCall: true, supportsImages: false, supportsReasoning: true, reasoning: { defaultSelection: selections[0], selections } });
function setup(overrides = {}) {
  const api = {
    listAgentTypes: vi.fn().mockResolvedValue({ agents: [{ name: definition.name, source: document.source, description: definition.description, enabled: true, available: true, baseRole: "worker", definitionVersion: "b".repeat(64) }] }),
    listModels: vi.fn().mockResolvedValue({ models: [model("deepseek", "deepseek-config", ["high", "max"]), model("volcengine", "volc-config", ["low", "high"])] }),
    readAgentDefinition: vi.fn().mockResolvedValue(document),
    saveAgentDefinition: vi.fn().mockResolvedValue(document),
    removeAgentDefinition: vi.fn().mockResolvedValue({ agents: [] }),
    showItemInFolder: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  Object.defineProperty(window, "eidosRuntime", { configurable: true, writable: true, value: api });
  return api;
}
function fill() {
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "new-worker" } });
  fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "负责验证" } });
  fireEvent.change(screen.getByLabelText("指令"), { target: { value: "报告验证结果。" } });
}

describe("智能体设置与编辑", () => {
  it("creates with inherited model and gives field errors without losing the draft", async () => {
    const api = setup(); const saved = vi.fn();
    render(<AgentEditorDialog onSaved={saved} onClose={vi.fn()} />);
    await screen.findByLabelText("指令");
    fireEvent.click(screen.getByRole("button", { name: "创建智能体" }));
    expect(api.saveAgentDefinition).not.toHaveBeenCalled();
    expect(screen.getByText("请填写智能体指令。")).toBeInTheDocument();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "创建智能体" }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith(document));
    expect(api.saveAgentDefinition).toHaveBeenCalledWith({ definition: expect.objectContaining({ name: "new-worker", instructions: "报告验证结果。", baseRole: "default" }) });
    expect(api.saveAgentDefinition.mock.calls[0]?.[0].definition.model).toBeUndefined();
  });

  it("uses the shared ModelSelector and saves the chosen provider, configuration and reasoning", async () => {
    const api = setup(); const close = vi.fn();
    render(<AgentEditorDialog onSaved={vi.fn()} onClose={close} />);
    await waitFor(() => expect(screen.queryByText("正在读取模型配置…")).not.toBeInTheDocument());
    fill(); fireEvent.click(screen.getByLabelText("指定模型"));
    fireEvent.click(screen.getByRole("button", { name: /deepseek-config，深度求索/ }));
    fireEvent.change(screen.getByRole("slider", { name: "deepseek-config 思考强度" }), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: /切换模型，当前为 deepseek-config/ }));
    fireEvent.click(screen.getByRole("radio", { name: "volc-config，火山引擎" }));
    const slider = screen.getByRole("slider", { name: "volc-config 思考强度" });
    expect(slider).toHaveValue("0");
    expect(screen.queryByText("最高")).not.toBeInTheDocument();
    fireEvent.change(slider, { target: { value: "1" } });
    fireEvent.keyDown(slider, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "创建智能体" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建智能体" }));
    await waitFor(() => expect(api.saveAgentDefinition).toHaveBeenCalledWith({ definition: expect.objectContaining({ model: { providerId: "volcengine", configId: "volc-config", reasoningSelection: "high" } }) }));
  });

  it("returns keyboard focus from the shared model menu to the editor", async () => {
    setup();
    render(<AgentEditorDialog onSaved={vi.fn()} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.queryByText("正在读取模型配置…")).not.toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("指定模型"));
    fireEvent.click(screen.getByRole("button", { name: /deepseek-config，深度求索/ }));
    const slider = screen.getByRole("slider", { name: "deepseek-config 思考强度" });
    slider.focus(); fireEvent.keyDown(slider, { key: "Tab" });
    expect(screen.getByLabelText("指令")).toHaveFocus();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });

  it("edits manual files with a version and shows conflicts in Chinese", async () => {
    const api = setup({ saveAgentDefinition: vi.fn().mockRejectedValue(new Error("EIDOS_RUNTIME_ERROR:AGENT_DEFINITION_CONFLICT")) });
    render(<AgentEditorDialog document={document} onSaved={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByLabelText("名称")).toHaveAttribute("readonly");
    fireEvent.change(screen.getByLabelText("指令"), { target: { value: "新的约束" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("智能体配置已经变化");
    expect(screen.getByLabelText("指令")).toHaveValue("新的约束");
    expect(api.saveAgentDefinition).toHaveBeenCalledWith({ source: document.source, expectedVersion: document.version, definition: { ...definition, instructions: "新的约束" } });
  });

  it("keeps missing models visible and permits switching back to inheritance", async () => {
    const api = setup({ listModels: vi.fn().mockResolvedValue({ models: [] }) });
    render(<AgentEditorDialog document={{ ...document, definition: { ...definition, model: { providerId: "gone", configId: "missing" } } }} onSaved={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("已保存的模型配置不可用或提供商不匹配，请重新选择。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    expect(api.saveAgentDefinition).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("继承父任务模型"));
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(api.saveAgentDefinition).toHaveBeenCalledWith(expect.objectContaining({ definition: expect.objectContaining({ model: null }) })));
  });

  it("preserves manual reasoning-only overrides until explicitly cleared", async () => {
    const api = setup();
    render(<AgentEditorDialog document={{ ...document, definition: { ...definition, model: { reasoningSelection: "high" } } }} onSaved={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(/单独指定思考强度：高/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(api.saveAgentDefinition).toHaveBeenCalledWith(expect.objectContaining({ definition: expect.objectContaining({ model: { reasoningSelection: "high" } }) })));
  });

  it("traps keyboard focus across select fields and closes with Escape", async () => {
    setup(); const close = vi.fn();
    render(<AgentEditorDialog onSaved={vi.fn()} onClose={close} />);
    const first = screen.getByLabelText("名称");
    const last = screen.getByRole("button", { name: "创建智能体" });
    last.focus(); fireEvent.keyDown(last, { key: "Tab" }); expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true }); expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: "Escape" }); expect(close).toHaveBeenCalledTimes(1);
  });

  it("lists manual definitions, edits and copies them, and keeps deletion behind confirmation", async () => {
    const api = setup();
    render(<AgentSettings ready onCountChange={vi.fn()} onShowToast={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "luna-work" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(await screen.findByRole("dialog", { name: "编辑智能体" })).toBeInTheDocument();
    expect(api.readAgentDefinition).toHaveBeenCalledWith(document.source);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByText("复制为新智能体"));
    expect(await screen.findByLabelText("名称")).toHaveValue("luna-work-copy");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "删除", hidden: true }));
    const confirm = await screen.findByRole("alertdialog");
    expect(api.removeAgentDefinition).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(api.removeAgentDefinition).toHaveBeenCalledWith({ source: document.source, expectedVersion: document.version }));
  });

  it("shows a useful empty state and creates from an intrinsic role without editing it", async () => {
    setup({ listAgentTypes: vi.fn().mockResolvedValue({ agents: [] }) });
    render(<AgentSettings ready onCountChange={vi.fn()} onShowToast={vi.fn()} />);
    expect(await screen.findByText("还没有自定义智能体")).toBeInTheDocument();
    fireEvent.click(screen.getByText("内置智能体 · 3"));
    fireEvent.click(screen.getAllByRole("button", { name: "复制为自定义智能体" })[2]!);
    expect(screen.getByLabelText("基础角色")).toHaveValue("worker");
    expect(screen.getByLabelText("名称")).toHaveValue("worker-custom");
  });
});
