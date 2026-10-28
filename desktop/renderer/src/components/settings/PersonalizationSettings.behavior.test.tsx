import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI, RuntimeStatus } from "../../contracts.js";
import type { PersonalizationResponse } from "../../../../shared/personalization.js";
import { PersonalizationSettings, discardPersonalizationDraft } from "./PersonalizationSettings.js";
import { SettingsPage } from "./SettingsPage.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
function initial(): PersonalizationResponse {
  return { revision: 0, preferences: { responseLanguage: "auto", customLanguage: "", tone: "default", responseLength: "auto", outputFormat: "auto", preferredName: "", role: "", familiarTopics: "", customInstructions: "" } };
}
function install() {
  const read = vi.fn().mockResolvedValue(initial());
  const save = vi.fn().mockImplementation(async request => ({ revision: request.expectedRevision + 1, preferences: request.preferences }));
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readPersonalization: read, savePersonalization: save, listModelPresets: vi.fn().mockResolvedValue({ providers: [] }) } as unknown as EidosRuntimeAPI });
  return { read, save };
}

function PageHost() {
  const [open, setOpen] = useState(true);
  const runtime: RuntimeStatus = { state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: false, storageHealth: { state: "ready" } };
  return open ? <SettingsPage initialCategory="personalization" runtime={runtime} plugins={[]} skills={[]} mcpServers={[]} pendingAction={undefined} onClose={() => setOpen(false)}
    onModelsChanged={vi.fn()} onImportPlugin={vi.fn()} onTogglePlugin={vi.fn()} onRemovePlugin={vi.fn()} onToggleSkill={vi.fn()} onRemoveSkill={vi.fn()} onToggleMcp={vi.fn()} onCreateMcp={vi.fn()} onUpdateMcp={vi.fn()} onRemoveMcp={vi.fn()} /> : <p>设置已关闭</p>;
}

afterEach(() => {
  discardPersonalizationDraft();
  vi.restoreAllMocks();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

describe("Personalization settings", () => {
  it("loads the three requested sections without an effective-instructions viewer or manual save button", async () => {
    install(); render(<PersonalizationSettings />);
    for (const name of ["语言与表达", "关于你", "自定义指令"]) {
      expect(await screen.findByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.queryByText("生效指令查看")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "回答语言" })).toHaveValue("auto");
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "还原修改" })).not.toBeInTheDocument();
    expect(screen.queryByText("所有修改已保存。")).not.toBeInTheDocument();
  });

  it("saves expression preferences, profile and instructions in real-time as one revision", async () => {
    const { save } = install(); const toast = vi.fn(); const user = userEvent.setup();
    render(<PersonalizationSettings onShowToast={toast} />);
    expect(await screen.findByRole("heading", { name: "语言与表达" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("回答语言"), "zh-CN");
    await user.selectOptions(screen.getByLabelText("语气"), "direct");
    await user.selectOptions(screen.getByLabelText("回答长度"), "concise");
    await user.selectOptions(screen.getByLabelText("输出格式偏好"), "conclusion_first");
    await user.type(screen.getByLabelText("称呼"), "小李");
    await user.type(screen.getByLabelText("角色"), "开发者");
    await user.type(screen.getByLabelText("熟悉的领域"), "TypeScript");
    const instructions = screen.getByRole("textbox", { name: "自定义指令", exact: true });
    await user.type(instructions, "请使用短句。");
    await user.tab();
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({
      preferences: expect.objectContaining({
        responseLanguage: "zh-CN",
        tone: "direct",
        responseLength: "concise",
        outputFormat: "conclusion_first",
        preferredName: "小李",
        role: "开发者",
        familiarTopics: "TypeScript",
        customInstructions: "请使用短句。",
      }),
    })));
    expect(toast).toHaveBeenCalledWith("个性化设置已保存，将用于新任务。", "success");
  });

  it("preserves the draft on save failure and permits a retry", async () => {
    const { save } = install(); save.mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup(); render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "关于你" })).toBeInTheDocument();
    const nameInput = screen.getByLabelText("称呼");
    await user.type(nameInput, "保留的草稿");
    await user.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("当前修改已保留");
    expect(nameInput).toHaveValue("保留的草稿");
    await user.type(nameInput, "!");
    await user.tab();
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  });

  it("disables editing until a failed initial read succeeds", async () => {
    const { read } = install(); read.mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup(); render(<PersonalizationSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取");
    expect(screen.getByLabelText("称呼")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await waitFor(() => expect(screen.getByLabelText("称呼")).toBeEnabled());
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("retains instructions rejected by the credential check so the user can correct them", async () => {
    const { save } = install(); save.mockRejectedValueOnce(new Error("SENSITIVE_CONTENT_REJECTED"));
    const user = userEvent.setup(); render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "自定义指令" })).toBeInTheDocument();
    const editor = screen.getByRole("textbox", { name: "自定义指令", exact: true });
    await user.type(editor, "待修正的指令");
    await user.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("请移除后再保存");
    expect(editor).toHaveValue("待修正的指令");
  });

  it("requires a custom language before auto-saving", async () => {
    const { save } = install(); const user = userEvent.setup(); render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "语言与表达" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("回答语言"), "custom");
    expect(save).not.toHaveBeenCalled();
    const customInput = screen.getByLabelText("其他回答语言");
    await user.type(customInput, "Español");
    await user.tab();
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({
      preferences: expect.objectContaining({ responseLanguage: "custom", customLanguage: "Español" }),
    })));
  });

  it("keeps a conflicted draft until the user chooses to discard and reload", async () => {
    const { read, save } = install(); save.mockRejectedValueOnce(new Error("PERSONALIZATION_CONFLICT"));
    const user = userEvent.setup(); render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "关于你" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("称呼"), "草稿");
    await user.tab();
    expect(await screen.findByRole("alert")).toHaveTextContent("其他位置更新");
    read.mockResolvedValue({ revision: 3, preferences: { ...initial().preferences, preferredName: "最新设置" } });
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByLabelText("称呼")).toHaveValue("草稿");
    expect(read).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await user.click(screen.getByRole("button", { name: "丢弃并重新读取" }));
    await waitFor(() => expect(screen.getByLabelText("称呼")).toHaveValue("最新设置"));
  });

  it("queues and commits subsequent edits while a save is in flight", async () => {
    const { save } = install();
    let complete!: (value: PersonalizationResponse) => void;
    save.mockImplementationOnce(() => new Promise<PersonalizationResponse>(resolve => { complete = resolve; }));
    const user = userEvent.setup(); render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "关于你" })).toBeInTheDocument();
    const nameInput = screen.getByLabelText("称呼");
    await user.type(nameInput, "第一版");
    await user.tab();
    expect(save).toHaveBeenCalledTimes(1);
    await user.type(nameInput, "加后缀");
    await user.tab();
    complete({ revision: 1, preferences: { ...initial().preferences, preferredName: "第一版" } });
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1]?.[0]).toMatchObject({ expectedRevision: 1, preferences: { preferredName: "第一版加后缀" } });
    expect(nameInput).toHaveValue("第一版加后缀");
  });

  it("saves Markdown instructions immediately on blur before the debounce expires", async () => {
    const { save } = install(); render(<PersonalizationSettings />);
    const editor = screen.getByRole("textbox", { name: "自定义指令", exact: true });
    await waitFor(() => expect(editor).toBeEnabled());
    fireEvent.change(editor, { target: { value: "失焦时立即保存" } });
    expect(save).not.toHaveBeenCalled();
    fireEvent.blur(editor);
    expect(save).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ preferences: expect.objectContaining({ customInstructions: "失焦时立即保存" }) })));
  });

  it("pauses automatic saving while the discard dialog is open", async () => {
    const { save } = install(); const user = userEvent.setup(); render(<PageHost />);
    const editor = screen.getByRole("textbox", { name: "自定义指令", exact: true });
    await waitFor(() => expect(editor).toBeEnabled());
    await user.type(editor, "确认丢弃的草稿");
    await user.keyboard("{Escape}");
    await screen.findByRole("alertdialog");
    await new Promise(resolve => setTimeout(resolve, 500));
    expect(save).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "丢弃并离开" }));
    expect(screen.getByText("设置已关闭")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("resumes automatic saving when the user cancels leaving", async () => {
    const { save } = install(); const user = userEvent.setup(); render(<PageHost />);
    const editor = screen.getByRole("textbox", { name: "自定义指令", exact: true });
    await waitFor(() => expect(editor).toBeEnabled());
    await user.type(editor, "继续编辑的草稿");
    await user.keyboard("{Escape}");
    await screen.findByRole("alertdialog");
    expect(save).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    expect(editor).toHaveValue("继续编辑的草稿");
  });

  it("does not automatically replay a failed auto-save without another edit", async () => {
    const { save } = install(); save.mockRejectedValue(new Error("offline"));
    const user = userEvent.setup(); render(<PersonalizationSettings />);
    await waitFor(() => expect(screen.getByLabelText("称呼")).toBeEnabled());
    await user.type(screen.getByLabelText("称呼"), "保留失败的草稿");
    await user.tab();
    await screen.findByRole("alert");
    await new Promise(resolve => setTimeout(resolve, 500));
    expect(save).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("称呼")).toHaveValue("保留失败的草稿");
  });

  it("preserves a draft while Runtime storage becomes unavailable and recovers", async () => {
    const { save } = install(); const user = userEvent.setup();
    const { rerender } = render(<PersonalizationSettings ready />);
    expect(await screen.findByRole("heading", { name: "自定义指令" })).toBeInTheDocument();
    const editor = screen.getByRole("textbox", { name: "自定义指令", exact: true });
    await user.type(editor, "离线时也要保留的指令");
    rerender(<PersonalizationSettings ready={false} />);
    expect(screen.getByRole("textbox", { name: "自定义指令", exact: true })).toHaveValue("离线时也要保留的指令");
    rerender(<PersonalizationSettings ready />);
    await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ preferences: expect.objectContaining({ customInstructions: "离线时也要保留的指令" }) })));
  });

  it("restores unsaved input after the Runtime gate unmounts the editor", async () => {
    const { read } = install(); const user = userEvent.setup();
    const first = render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "关于你" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("称呼"), "重启前的草稿");
    first.unmount();
    read.mockResolvedValue({ ...initial(), revision: 2 });
    render(<PersonalizationSettings />);
    expect(screen.getByLabelText("称呼")).toHaveValue("重启前的草稿");
    await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("alert")).toHaveTextContent("其他位置更新");
  });

  it("restores the current Runtime configuration rather than a stale cached baseline", async () => {
    const { read } = install(); const user = userEvent.setup();
    const first = render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "关于你" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("称呼"), "未保存草稿");
    first.unmount();
    read.mockResolvedValue({ revision: 3, preferences: { ...initial().preferences, preferredName: "其他位置的新设置" } });
    render(<PersonalizationSettings />);
    expect(await screen.findByRole("alert")).toHaveTextContent("其他位置更新");
    expect(screen.getByLabelText("称呼")).toHaveValue("未保存草稿");
    await user.click(screen.getByRole("button", { name: "重新读取" }));
    await user.click(screen.getByRole("button", { name: "丢弃并重新读取" }));
    await waitFor(() => expect(screen.getByLabelText("称呼")).toHaveValue("其他位置的新设置"));
  });

  it("recognizes a save committed while the editor was unmounted", async () => {
    const { read, save } = install(); const user = userEvent.setup();
    let complete!: (value: PersonalizationResponse) => void;
    save.mockImplementation(() => new Promise<PersonalizationResponse>(resolve => { complete = resolve; }));
    const first = render(<PersonalizationSettings />);
    expect(await screen.findByRole("heading", { name: "关于你" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("称呼"), "已在后台保存");
    await user.tab();
    first.unmount();
    const committed = { revision: 1, preferences: { ...initial().preferences, preferredName: "已在后台保存" } };
    complete(committed); read.mockResolvedValue(committed);
    render(<PersonalizationSettings />);
    expect(await screen.findByLabelText("称呼")).toHaveValue("已在后台保存");
  });

  it("continues the latest draft after an older save commits during unmount", async () => {
    const { read, save } = install(); const user = userEvent.setup();
    let complete!: (value: PersonalizationResponse) => void;
    save.mockImplementationOnce(() => new Promise<PersonalizationResponse>(resolve => { complete = resolve; }));
    const first = render(<PersonalizationSettings />);
    const name = screen.getByLabelText("称呼");
    await waitFor(() => expect(name).toBeEnabled());
    await user.type(name, "A"); await user.tab();
    expect(save).toHaveBeenCalledTimes(1);
    await user.type(name, "B"); await user.tab();
    first.unmount();
    const committed = { revision: 1, preferences: { ...initial().preferences, preferredName: "A" } };
    read.mockResolvedValue(committed); complete(committed);
    render(<PersonalizationSettings />);
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1]?.[0]).toMatchObject({ expectedRevision: 1, preferences: { preferredName: "AB" } });
    expect(screen.getByLabelText("称呼")).toHaveValue("AB");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("protects unsaved changes when switching categories or pressing Escape", async () => {
    install(); const user = userEvent.setup(); const onClose = vi.fn();
    const runtime: RuntimeStatus = { state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: false, storageHealth: { state: "ready" } };
    render(<SettingsPage initialCategory="personalization" runtime={runtime} plugins={[]} skills={[]} mcpServers={[]} pendingAction={undefined} onClose={onClose}
      onModelsChanged={vi.fn()} onImportPlugin={vi.fn()} onTogglePlugin={vi.fn()} onRemovePlugin={vi.fn()} onToggleSkill={vi.fn()} onRemoveSkill={vi.fn()} onToggleMcp={vi.fn()} onCreateMcp={vi.fn()} onUpdateMcp={vi.fn()} onRemoveMcp={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "语言与表达" })).toBeInTheDocument();
    // Select custom language without entering one -> creates an un-saveable dirty draft
    await user.selectOptions(screen.getByLabelText("回答语言"), "custom");
    await user.click(screen.getByRole("tab", { name: "模型" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByLabelText("回答语言")).toHaveValue("custom");
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "丢弃并离开" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
