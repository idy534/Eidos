import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InputContextProvider } from "./InputContext.js";
import { InputPicker } from "./InputPicker.js";
import type { EidosRuntimeAPI } from "../contracts.js";
import type { InputReference } from "../../../shared/input-context.js";

const reference: InputReference = {
  id: "a".repeat(64),
  kind: "file",
  label: "notes.txt",
  source: "/workspace/notes.txt",
  sha256: "b".repeat(64),
  status: "content",
  size: 12,
};

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("InputPicker behavior", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupRuntime(overrides: Record<string, unknown> = {}) {
    const api = {
      listSkills: vi.fn().mockResolvedValue({ skills: [] }),
      listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
      listMcpServers: vi.fn().mockResolvedValue({ servers: [] }),
      listSessions: vi.fn().mockResolvedValue({ items: [] }),
      listWorkspaceDirectory: vi.fn().mockResolvedValue({ path: ".", entries: [], truncated: false }),
      pickInputPaths: vi.fn().mockResolvedValue([]),
      prepareInput: vi.fn().mockResolvedValue(reference),
      onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      ...overrides,
    } as unknown as EidosRuntimeAPI;
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: api,
    });
    return api;
  }

  const pickerProps = {
    query: "",
    inline: false,
    onQuery: vi.fn(),
    onChoose: vi.fn(),
    onClose: vi.fn(),
    onCommand: vi.fn(),
  };

  it("shows commands and skills in commands mode, without /skills, and dispatches selection", async () => {
    setupRuntime({
      listSkills: vi.fn().mockResolvedValue({
        skills: [{
          qualifiedId: "system:plugin-creator",
          name: "plugin-creator",
          description: "Create plugins",
          sourceKind: "system",
          pluginId: "",
          available: true,
          enabled: true,
        }],
      }),
    });
    const onCommand = vi.fn();
    const onChoose = vi.fn();

    render(<InputPicker {...pickerProps} mode="commands" onCommand={onCommand} onChoose={onChoose} />);

    const planOption = await screen.findByRole("option", { name: /\/plan/ });
    expect(screen.queryByRole("option", { name: /\/skills/ })).not.toBeInTheDocument();

    expect(await screen.findByText("技能")).toBeInTheDocument();
    const skillOption = screen.getByRole("option", { name: /plugin-creator/ });

    fireEvent.click(planOption);
    expect(onCommand).toHaveBeenCalledWith("plan");

    fireEvent.click(skillOption);
    expect(onChoose).toHaveBeenCalledWith({ kind: "skill", source: "system:plugin-creator" });
  });

  it("prepares a file or folder selected by the quick action, adds it to the current draft, and closes the picker", async () => {
    const api = setupRuntime({
      pickInputPaths: vi.fn()
        .mockResolvedValueOnce(["/workspace/notes.txt"])
        .mockResolvedValueOnce(["/workspace/docs"]),
    });
    const onAdd = vi.fn();
    const onClose = vi.fn();

    render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={onAdd}
        onSettings={vi.fn()}
      >
        <InputPicker {...pickerProps} onClose={onClose} mode="all" />
      </InputContextProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "添加文件" }));

    await waitFor(() => expect(onAdd).toHaveBeenCalledWith("session-1", reference));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(api.prepareInput).toHaveBeenCalledWith({
      kind: "file",
      source: "/workspace/notes.txt",
      sessionId: "session-1",
    });

    fireEvent.click(screen.getByRole("button", { name: "添加文件夹" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(2));
    expect(api.pickInputPaths).toHaveBeenCalledWith(true);
  });

  it("keeps unavailable skill candidates disabled", async () => {
    setupRuntime({
      listSkills: vi.fn().mockResolvedValue({
        skills: [{
          qualifiedId: "user:disabled",
          name: "Disabled Skill",
          description: "Unavailable",
          sourceKind: "user",
          pluginId: "",
          available: false,
          enabled: true,
        }],
      }),
    });
    const onChoose = vi.fn();

    render(<InputPicker {...pickerProps} mode="skill" onChoose={onChoose} />);

    const option = await screen.findByRole("option", { name: /Disabled Skill/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(option);
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("renders action buttons in correct order with disabled goal mode and triggers Plan mode", async () => {
    setupRuntime();
    const onSelectPlanMode = vi.fn();
    const onClose = vi.fn();

    render(
      <InputPicker
        {...pickerProps}
        mode="all"
        onSelectPlanMode={onSelectPlanMode}
        onClose={onClose}
      />,
    );

    const fileBtn = await screen.findByRole("button", { name: "添加文件" });
    const folderBtn = screen.getByRole("button", { name: "添加文件夹" });
    const planBtn = screen.getByRole("button", { name: "Plan 模式" });
    const goalBtn = screen.getByRole("button", { name: "目标模式" });

    expect(fileBtn).toBeInTheDocument();
    expect(folderBtn).toBeInTheDocument();
    expect(planBtn).toBeInTheDocument();
    expect(goalBtn).toBeDisabled();

    fireEvent.click(planBtn);
    expect(onSelectPlanMode).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("toggles collapsible search bar when clicking search trigger and collapses on 收起", async () => {
    setupRuntime();
    const onQuery = vi.fn();

    render(<InputPicker {...pickerProps} mode="all" onQuery={onQuery} />);

    const searchTrigger = await screen.findByRole("button", { name: "搜索引用" });
    expect(screen.queryByPlaceholderText("搜索名称、Skill、扩展…")).not.toBeInTheDocument();

    fireEvent.click(searchTrigger);

    const searchInput = screen.getByRole("textbox", { name: "搜索引用" });
    expect(searchInput).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "添加文件" })).not.toBeInTheDocument();

    const collapseBtn = screen.getByRole("button", { name: "收起搜索" });
    fireEvent.click(collapseBtn);

    expect(screen.queryByPlaceholderText("搜索名称、Skill、扩展…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加文件" })).toBeInTheDocument();
  });

  it("handles Escape key to clear query then collapse search", async () => {
    setupRuntime();
    const onQuery = vi.fn();

    const { rerender } = render(
      <InputPicker {...pickerProps} query="test" mode="all" onQuery={onQuery} />,
    );

    const searchInput = await screen.findByRole("textbox", { name: "搜索引用" });
    expect(searchInput).toBeInTheDocument();

    fireEvent.keyDown(searchInput, { key: "Escape" });
    expect(onQuery).toHaveBeenCalledWith("");

    rerender(<InputPicker {...pickerProps} query="" mode="all" onQuery={onQuery} />);
    const emptyInput = screen.getByRole("textbox", { name: "搜索引用" });
    fireEvent.keyDown(emptyInput, { key: "Escape" });

    expect(screen.queryByPlaceholderText("搜索名称、Skill、扩展…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加文件" })).toBeInTheDocument();
  });

  it("renders redesigned category tabs and toggles active selection", async () => {
    setupRuntime();

    render(<InputPicker {...pickerProps} mode="all" />);

    const tabs = ["全部", "文件", "Skill", "MCP", "插件", "会话"];
    for (const label of tabs) {
      expect(await screen.findByRole("tab", { name: label })).toBeInTheDocument();
    }

    const skillTab = screen.getByRole("tab", { name: "Skill" });
    expect(skillTab).toHaveAttribute("aria-selected", "false");

    fireEvent.click(skillTab);
    expect(skillTab).toHaveAttribute("aria-selected", "true");
  });
  it("selects custom agents through the existing reference picker and shows provider details", async () => {
    setupRuntime({ listAgentTypes: vi.fn().mockResolvedValue({ agents: [{
      name: "luna-work", description: "负责实现", source: "/data/agents/luna-work.toml",
      baseRole: "worker", providerId: "volcengine", modelId: "deepseek-v4.1-flash", enabled: true, available: true,
    }] }) });
    const onChoose = vi.fn();
    render(<InputPicker {...pickerProps} mode="all" onChoose={onChoose} />);
    fireEvent.click(screen.getByRole("tab", { name: "智能体" }));
    const option = await screen.findByRole("option", { name: /luna-work/ });
    expect(option).toHaveTextContent("volcengine / deepseek-v4.1-flash");
    fireEvent.click(option);
    expect(onChoose).toHaveBeenCalledWith({ kind: "agent", source: "luna-work" });
  });

  it("keeps unavailable agent definitions visible with a concrete reason", async () => {
    setupRuntime({ listAgentTypes: vi.fn().mockResolvedValue({ agents: [{
      name: "missing-model", description: "调查", source: "/data/agents/missing.toml",
      baseRole: "explorer", enabled: true, available: false, reason: "模型尚未配置。",
    }] }) });
    const onChoose = vi.fn();
    render(<InputPicker {...pickerProps} mode="all" onChoose={onChoose} />);
    const option = await screen.findByRole("option", { name: /missing-model/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveTextContent("模型尚未配置。");
    fireEvent.click(option);
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("lists each project folder for a draft and keeps equal filenames distinct", async () => {
    const list = vi.fn(async (_session: string, _path: string, _limit: number, root: string) => ({
      path: ".", truncated: false, entries: [{ name: "README.md", relativePath: "README.md", kind: "file" }],
    }));
    setupRuntime({ listWorkspaceDirectory: list });
    const onChoose = vi.fn();
    render(<InputContextProvider sessionId="draft-multi" workspaceRoot="/work/main" projectId="project-multi"
      workspaceRoots={["/work/main", "/work/second"]} ready onAdd={vi.fn()} onSettings={vi.fn()}>
      <InputPicker {...pickerProps} mode="all" onChoose={onChoose} />
    </InputContextProvider>);
    const files = await screen.findAllByRole("option", { name: /README.md/ });
    expect(files).toHaveLength(2);
    expect(list).toHaveBeenCalledWith("draft-multi", ".", 100, "/work/main", "project-multi");
    expect(list).toHaveBeenCalledWith("draft-multi", ".", 100, "/work/second", "project-multi");
    fireEvent.click(files[1]!);
    expect(onChoose).toHaveBeenCalledWith({ kind: "file", source: "/work/second/README.md" });
  });

});
