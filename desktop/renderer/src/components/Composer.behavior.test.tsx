import { useState } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Composer, type ComposerProps } from "./Composer.js";
import { setInlineEditorSelectionOffsets } from "./InlineRichEditor.js";
import { InputContextProvider } from "./InputContext.js";
import {
  canonicalizeInlineText,
  insertInlineReferenceAtSelection,
  isInlineReference,
  removeInlineReferenceTokenById,
} from "../../../shared/inline-references.js";
import type { ModelListResult, Run } from "../contracts.js";
import type { InputReference } from "../../../shared/input-context.js";

const mockModelList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [
    {
      id: "deepseek-v4-flash", name: "DeepSeek-V4 Flash", vendor: "DeepSeek",
      provider: "deepseek", url: "https://api.deepseek.com/chat/completions",
      supportsToolCall: true, supportsImages: false, supportsReasoning: true,
      reasoning: { defaultSelection: "high", selections: ["high", "max"] },
    },
  ],
};

const defaultProps: ComposerProps = {
  composerMode: "idle",
  activeRun: undefined,
  input: "",
  modelList: mockModelList,
  selectedModelId: "deepseek-v4-flash",
  contextUsage: undefined,
  modelConfigured: true,
  modelLoading: false,
  isSubmitting: false,
  submitKind: undefined,
  cancelingRunId: undefined,
  onInputChange: vi.fn(),
  onSubmit: vi.fn(),
  onCancel: vi.fn(),
  onModelChange: vi.fn(),
  onOpenModelSettings: vi.fn(),
};

const composerReference: InputReference = {
  id: "a".repeat(64),
  kind: "file",
  label: "notes.txt",
  source: "/workspace/notes.txt",
  sha256: "b".repeat(64),
  status: "content",
  size: 12,
};

describe("Composer DOM interaction & state behavior", () => {
  it("creates and references an agent without sending or discarding the existing draft", async () => {
    const saved = { source: "/data/agents/new-agent.toml", version: "b".repeat(64), text: "", definition: { schemaVersion: 1, name: "new-agent", description: "负责调查", instructions: "报告源码证据。" } };
    const add = vi.fn(); const settings = vi.fn(); const submit = vi.fn();
    const agentReference = { ...composerReference, kind: "agent", label: "new-agent", source: "new-agent" };
    const api = {
      listSkills: vi.fn().mockResolvedValue({ skills: [] }), listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
      listMcpServers: vi.fn().mockResolvedValue({ servers: [] }), listSessions: vi.fn().mockResolvedValue({ items: [] }),
      listAgentTypes: vi.fn().mockResolvedValue({ agents: [] }), listModels: vi.fn().mockResolvedValue(mockModelList),
      listWorkspaceDirectory: vi.fn().mockResolvedValue({ entries: [], truncated: false }), onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      saveAgentDefinition: vi.fn().mockResolvedValue(saved), prepareInput: vi.fn().mockResolvedValue(agentReference),
    };
    Object.defineProperty(window, "eidosRuntime", { configurable: true, writable: true, value: api });
    render(<InputContextProvider sessionId="session-1" workspaceRoot="/workspace" ready onAdd={add} onSettings={settings}>
      <Composer {...defaultProps} input="保留已有草稿" onSubmit={submit} />
    </InputContextProvider>);
    fireEvent.click(screen.getByRole("button", { name: "添加引用" }));
    fireEvent.click(await screen.findByRole("tab", { name: "智能体" }));
    fireEvent.click(screen.getByRole("button", { name: "+ 创建智能体" }));
    expect(await screen.findByRole("dialog", { name: "创建智能体" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "new-agent" } });
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "负责调查" } });
    fireEvent.change(screen.getByLabelText(/Agent\.md|指令/), { target: { value: "报告源码证据。" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并引用" }));
    await waitFor(() => expect(add).toHaveBeenCalledWith("session-1", agentReference));
    expect(api.prepareInput).toHaveBeenCalledWith({ kind: "agent", source: "new-agent", sessionId: "session-1" });
    expect(screen.getByRole("textbox")).toHaveValue("保留已有草稿");
    expect(submit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "添加引用" }));
    fireEvent.click(await screen.findByRole("tab", { name: "智能体" }));
    fireEvent.click(screen.getByRole("button", { name: "管理智能体" }));
    expect(settings).toHaveBeenCalledWith("agents");
  });
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("modelLoading retains draft editing and disables execution", () => {
    render(<Composer {...defaultProps} modelLoading={true} input="Some text" />);

    const textarea = screen.getByPlaceholderText("正在加载模型配置…");
    const submitBtn = screen.getByRole("button", { name: "加载中…" });

    expect(textarea).toBeEnabled();
    expect(submitBtn).toBeDisabled();
  });

  it("Missing selectedModelId (undefined) disables submit button", () => {
    render(<Composer {...defaultProps} selectedModelId={undefined} input="Valid task text" />);

    const submitBtn = screen.getByRole("button", { name: "开始" });
    expect(submitBtn).toBeDisabled();
  });

  it("keeps the configured model selector available between completed Turns", () => {
    const onModelChange = vi.fn();
    const modelList: ModelListResult = {
      ...mockModelList,
      models: [
        ...mockModelList.models,
        {
          id: "glm-5.3-flash", name: "GLM 5.3 Flash", vendor: "Volcengine",
          provider: "volcengine", url: "https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
          supportsToolCall: true, supportsImages: false, supportsReasoning: true,
          reasoning: { defaultSelection: "max", selections: ["low", "high", "max"] },
        },
      ],
    };
    render(<Composer {...defaultProps} modelList={modelList} onModelChange={onModelChange} />);

    fireEvent.click(screen.getByRole("button", { name: /打开模型和思考强度菜单/ }));
    fireEvent.click(screen.getByRole("button", { name: /切换模型/ }));
    fireEvent.click(screen.getByRole("radio", { name: /GLM 5\.3 Flash/ }));

    expect(onModelChange).toHaveBeenCalledWith("glm-5.3-flash");
  });

  it("keeps model selection available when the selected model has no configurable reasoning choices", () => {
    const modelList: ModelListResult = {
      ...mockModelList,
      models: mockModelList.models.map((model) => ({ ...model, reasoning: null })),
    };
    render(<Composer {...defaultProps} modelList={modelList} />);

    fireEvent.click(screen.getByRole("button", { name: /DeepSeek-V4 Flash.*选择模型/ }));

    expect(screen.getByRole("dialog", { name: "选择模型" })).toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });

  it("shows provider, estimated, and empty context usage states beside the model", () => {
    const { rerender } = render(
      <Composer
        {...defaultProps}
        contextUsage={{
          activeTokens: 185_000,
          windowTokens: 258_000,
          percentUsed: 71.7,
          source: "provider",
        }}
      />,
    );
    expect(screen.getByText("上下文 71.7% · 185K / 258K")).toBeInTheDocument();

    rerender(<Composer {...defaultProps} contextUsage={{
      activeTokens: 185_000,
      windowTokens: 803_000,
      percentUsed: 23,
      source: "estimated",
    }} />);
    expect(screen.getByText("上下文 ≈23% · ≈185K / 803K")).toBeInTheDocument();

    rerender(<Composer {...defaultProps} contextUsage={undefined} />);
    expect(screen.getByText("上下文 --")).toBeInTheDocument();
  });

  it("hides the context indicator without a Run and shows it when a Run exists", () => {
    const { rerender } = render(<Composer {...defaultProps} showContextIndicator={false} />);

    expect(screen.queryByRole("region", { name: "上下文 --" })).not.toBeInTheDocument();

    rerender(<Composer {...defaultProps} showContextIndicator />);
    expect(screen.getByRole("region", { name: "上下文 --" })).toBeInTheDocument();
  });

  it("renders project context above the input and defaults execution mode to Local", () => {
    const onExecutionModeChange = vi.fn();
    const onBranchChange = vi.fn();
    const onLeaveProject = vi.fn();
    render(
      <Composer
        {...defaultProps}
        project={{ id: "project-a", workspaceRoot: "/workspace/star-hub", gitAvailable: true }}
        executionMode={undefined}
        branch="dev-830-xl"
        branches={["main", "dev-830-xl"]}
        onBranchChange={onBranchChange}
        onExecutionModeChange={onExecutionModeChange}
        onLeaveProject={onLeaveProject}
      />,
    );

    expect(screen.getByText("star-hub")).toBeInTheDocument();
    expect(screen.getByLabelText("本地分支")).toHaveValue("dev-830-xl");
    expect(screen.getByLabelText("执行方式")).toHaveValue("local");
    const leaveProjectButton = screen.getByRole("button", { name: "不在项目中工作" });
    expect(leaveProjectButton).toHaveClass("composer-context-project-action");
    expect(leaveProjectButton.querySelector(".composer-context-project-close")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("执行方式"), { target: { value: "worktree" } });
    expect(onExecutionModeChange).toHaveBeenCalledWith("worktree");
    fireEvent.change(screen.getByLabelText("本地分支"), { target: { value: "main" } });
    expect(onBranchChange).toHaveBeenCalledWith("main");
    fireEvent.click(leaveProjectButton);
    expect(onLeaveProject).toHaveBeenCalledTimes(1);
  });

  it("hides execution mode and branch for a non-Git project", () => {
    render(
      <Composer
        {...defaultProps}
        project={{ id: "project-a", workspaceRoot: "/workspace/plain", gitAvailable: false }}
        executionMode="local"
        branch="main"
        onExecutionModeChange={vi.fn()}
        onLeaveProject={vi.fn()}
      />,
    );

    expect(screen.getByText("plain")).toBeInTheDocument();
    expect(screen.queryByLabelText("执行方式")).not.toBeInTheDocument();
    expect(screen.queryByText("main")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "不在项目中工作" })).toBeInTheDocument();
  });

  it("offers project selection for a new projectless conversation", () => {
    const onSelectProject = vi.fn();
    render(
      <Composer
        {...defaultProps}
        project={null}
        onSelectProject={onSelectProject}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择项目" }));
    expect(onSelectProject).toHaveBeenCalledTimes(1);
  });

  it("empty local model configuration disables submit and guides to settings", () => {
    const onOpenModelSettings = vi.fn();
    render(
      <Composer
        {...defaultProps}
        modelConfigured={false}
        input="Valid task text"
        onOpenModelSettings={onOpenModelSettings}
      />,
    );

    const submitBtn = screen.getByRole("button", { name: "开始" });
    expect(submitBtn).toBeDisabled();
    expect(screen.getByPlaceholderText("请先在设置中添加模型")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "前往模型设置" }));
    expect(onOpenModelSettings).toHaveBeenCalledTimes(1);
  });

  it("read-only execution retains draft editing and disables submission", () => {
    render(<Composer {...defaultProps} composerMode="read_only" input="Valid task text" />);

    const textarea = screen.getByPlaceholderText("暂无法执行，可继续编辑草稿");
    const submitBtn = screen.getByRole("button", { name: "开始" });

    expect(textarea).toBeEnabled();
    expect(submitBtn).toBeDisabled();
  });

  it.each(["running", "waiting_approval", "finalizing"] as const)("%s uses one action button for queueing or stopping", (status) => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    const activeRun: Run = { id: "active", sessionId: "s", modelId: "deepseek-v4-flash", status,
      modelStepCount: 1, createdAt: 1, updatedAt: 1, allowedActions: ["cancel"] };
    const { rerender } = render(<Composer {...defaultProps} composerMode={status} activeRun={activeRun}
      input="Next task" onSubmit={onSubmit} onCancel={onCancel} />);
    expect(screen.getByRole("textbox")).toBeEnabled();
    const submit = screen.getByRole("button", { name: "排队" });
    expect(submit).toBeEnabled();
    expect(screen.queryByRole("button", { name: "取消 Run" })).not.toBeInTheDocument();
    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledTimes(1);

    if (status === "running") {
      rerender(<Composer {...defaultProps} composerMode={status} activeRun={activeRun}
        input="" onSubmit={onSubmit} onCancel={onCancel} />);
      expect(screen.getByRole("button", { name: "取消 Run" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "排队" })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "取消 Run" }));
      expect(onCancel).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("button", { name: "开始" })).not.toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /打开模型和思考强度菜单/ })).toBeEnabled();
  });

  it("shows the approval mode selector and reports the next Run mode", () => {
    const onApprovalModeChange = vi.fn();
    const { rerender } = render(
      <Composer
        {...defaultProps}
        approvalMode="manual"
        onApprovalModeChange={onApprovalModeChange}
      />,
    );

    const selector = screen.getByRole("button", { name: "审批模式：请求审批" });
    expect(selector).toHaveTextContent("请求审批");

    fireEvent.click(selector);
    const autoReview = screen.getByRole("radio", { name: /替我审批.*推荐/ });
    expect(autoReview).toBeInTheDocument();
    fireEvent.click(autoReview);
    expect(onApprovalModeChange).toHaveBeenCalledWith("auto_review");

    rerender(
      <Composer
        {...defaultProps}
        approvalMode="full_access"
        onApprovalModeChange={onApprovalModeChange}
      />,
    );
    expect(screen.getByRole("button", { name: "审批模式：完全访问" })).toBeInTheDocument();

    rerender(
      <Composer
        {...defaultProps}
        composerMode="waiting_approval"
        approvalMode="auto_review"
        onApprovalModeChange={onApprovalModeChange}
      />,
    );
    expect(screen.getByRole("button", { name: "审批模式：替我审批" })).toBeEnabled();
  });

  it("starting mode presents starting loading state", () => {
    render(
      <Composer
        {...defaultProps}
        composerMode="starting"
        input="New task"
        submitKind="start"
      />,
    );

    const submitBtn = screen.getByRole("button", { name: "启动中…" });
    expect(submitBtn).toBeDisabled();
  });

  it("running mode presents Cancel button when allowed and hides when not allowed", () => {
    const activeRunAllowed: Run = {
      id: "run-1",
      sessionId: "session-1",
      status: "running",
      modelId: "deepseek-v4-flash",
      modelStepCount: 1,
      createdAt: 1000,
      startedAt: 1000,
      updatedAt: 1000,
      allowedActions: ["cancel"],
    };

    const { rerender } = render(
      <Composer
        {...defaultProps}
        composerMode="running"
        activeRun={activeRunAllowed}
        input=""
      />,
    );

    expect(screen.getByRole("button", { name: "取消 Run" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /打开模型和思考强度菜单/ })).toBeEnabled();

    const activeRunDisallowed: Run = {
      ...activeRunAllowed,
      allowedActions: [],
    };

    rerender(
      <Composer
        {...defaultProps}
        composerMode="running"
        activeRun={activeRunDisallowed}
        input=""
      />,
    );

    expect(screen.queryByRole("button", { name: "取消 Run" })).not.toBeInTheDocument();
  });

  it("Empty input cannot submit", () => {
    render(<Composer {...defaultProps} input="   " />);

    const submitBtn = screen.getByRole("button", { name: "开始" });
    expect(submitBtn).toBeDisabled();
  });

  it("opens the input picker from the add button when a draft context is ready", async () => {
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: {
        listSkills: vi.fn().mockResolvedValue({ skills: [] }),
        listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
        listMcpServers: vi.fn().mockResolvedValue({ servers: [] }),
        listSessions: vi.fn().mockResolvedValue({ items: [] }),
        listWorkspaceDirectory: vi.fn().mockResolvedValue({ path: ".", entries: [], truncated: false }),
        onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      },
    });
    render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <Composer {...defaultProps} />
      </InputContextProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "添加引用" }));

    expect(await screen.findByRole("listbox", { name: "引用候选" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "添加文件" })).toBeInTheDocument();
  });

  it.each([
    ["/sk", "快捷指令"],
    ["$de", "选择 Skill"],
    ["@no", "选择引用"],
  ])("opens the matching inline picker for %s", async (value, title) => {
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: {
        listSkills: vi.fn().mockResolvedValue({ skills: [] }),
        listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
        listMcpServers: vi.fn().mockResolvedValue({ servers: [] }),
        listSessions: vi.fn().mockResolvedValue({ items: [] }),
        listWorkspaceDirectory: vi.fn().mockResolvedValue({ path: ".", entries: [], truncated: false }),
        onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      },
    });
    render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <Composer {...defaultProps} />
      </InputContextProvider>,
    );

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value, selectionStart: value.length },
    });

    expect(await screen.findByText(title)).toBeInTheDocument();
  });

  it("allows selecting a skill candidate directly from / shortcut and consumes the mention", async () => {
    const onAdd = vi.fn();
    const onInputChange = vi.fn();
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: {
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
        listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
        listMcpServers: vi.fn().mockResolvedValue({ servers: [] }),
        listSessions: vi.fn().mockResolvedValue({ items: [] }),
        listWorkspaceDirectory: vi.fn().mockResolvedValue({ path: ".", entries: [], truncated: false }),
        prepareInput: vi.fn().mockResolvedValue({
          id: "skill-1",
          kind: "skill",
          label: "plugin-creator",
          source: "system:plugin-creator",
          status: "content",
        }),
        onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      },
    });

    function Wrapper() {
      const [input, setInput] = useState("");
      return (
        <InputContextProvider
          sessionId="session-1"
          workspaceRoot="/workspace"
          ready
          onAdd={onAdd}
          onSettings={vi.fn()}
        >
          <Composer
            {...defaultProps}
            input={input}
            onInputChange={(value) => {
              setInput(value);
              onInputChange(value);
            }}
          />
        </InputContextProvider>
      );
    }
    render(<Wrapper />);

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "/plug", selectionStart: 5 },
    });

    const skillOption = await screen.findByRole("option", { name: /plugin-creator/ });
    fireEvent.click(skillOption);

    await waitFor(() => {
      expect(onAdd).toHaveBeenCalledWith("session-1", expect.objectContaining({
        kind: "skill",
        source: "system:plugin-creator",
      }));
    });
    expect(onInputChange).toHaveBeenCalledWith("");
  });

  it("allows a reference-only draft to submit", () => {
    const onSubmit = vi.fn();
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: { onInputQuote: vi.fn().mockReturnValue(vi.fn()) },
    });
    render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <Composer {...defaultProps} references={[composerReference]} onSubmit={onSubmit} />
      </InputContextProvider>,
    );

    const submit = screen.getByRole("button", { name: "开始" });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("IME composition (isComposing) ignores Enter key submission", () => {
    const onSubmitSpy = vi.fn();
    render(<Composer {...defaultProps} input="输入中" onSubmit={onSubmitSpy} />);

    const textarea = screen.getByRole("textbox");

    // Simulate Enter while IME composition is active (e.g. typing Chinese characters)
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });
    expect(onSubmitSpy).not.toHaveBeenCalled();

    // Plain Enter submits
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: false });
    expect(onSubmitSpy).toHaveBeenCalledTimes(1);
  });

  it("Shift+Enter does not submit", () => {
    const onSubmitSpy = vi.fn();
    render(<Composer {...defaultProps} input="Line 1" onSubmit={onSubmitSpy} />);

    const textarea = screen.getByRole("textbox");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(onSubmitSpy).not.toHaveBeenCalled();
  });

  it("automatically focuses textarea when input transitions from disabled to enabled after run completion", () => {
    let rafCallback: FrameRequestCallback | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafCallback = cb;
      return 123;
    });

    const { rerender } = render(
      <Composer {...defaultProps} draftReady={false} input="" />,
    );

    const textarea = screen.getByRole("textbox");
    expect(textarea).toBeDisabled();

    // Rerender when run finishes and composerMode returns to idle
    rerender(<Composer {...defaultProps} composerMode="idle" input="" />);

    expect(textarea).not.toBeDisabled();
    expect(rafCallback).toBeDefined();

    rafCallback!(100);
    expect(textarea).toHaveFocus();
  });

  it("unmount cancels pending focus animation frame", () => {
    let rafCallback: FrameRequestCallback | undefined;
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame");
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafCallback = cb;
      return 456;
    });

    const { rerender, unmount } = render(
      <Composer {...defaultProps} draftReady={false} input="" />,
    );

    rerender(<Composer {...defaultProps} composerMode="idle" input="" />);
    expect(rafCallback).toBeDefined();

    unmount();
    expect(cancelSpy).toHaveBeenCalledWith(456);
  });

  it("keeps the same focused textarea while editing and clearing a multiline draft", () => {
    const { rerender } = render(<Composer {...defaultProps} input={"Line 1\nLine 2\nLine 3"} />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    textarea.focus();
    textarea.setSelectionRange(3, 3);

    rerender(<Composer {...defaultProps} input={"Line\nLine 2\nLine 3"} />);
    expect(screen.getByRole("textbox")).toBe(textarea);
    expect(textarea).toHaveFocus();
    expect(textarea).toHaveValue("Line\nLine 2\nLine 3");

    rerender(<Composer {...defaultProps} input="" />);
    expect(screen.getByRole("textbox")).toBe(textarea);
    expect(textarea).toHaveFocus();
    expect(textarea).toHaveValue("");
  });

  it("splits shelf attachments above input and renders inline tokens inside rich editor with atomic Backspace deletion", () => {
    const onRemoveReference = vi.fn();
    const onInputChange = vi.fn();
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: {
        onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      },
    });
    const skillRef: InputReference = {
      ...composerReference,
      id: "skill-ref-1",
      kind: "skill",
      label: "frontend-design",
      source: "skills/frontend-design",
    };
    const historyRef: InputReference = {
      ...composerReference,
      id: "history-ref-2",
      kind: "history",
      label: "上次对话",
      source: "session-prev",
    };

    const inlineDraft = "先参考 [$frontend-design](ref:skill-ref-1) 再结合 [@上次对话](ref:history-ref-2) 请继续优化";
    const { container, rerender } = render(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <Composer
          {...defaultProps}
          input={inlineDraft}
          references={[composerReference, skillRef, historyRef]}
          onInputChange={onInputChange}
          onRemoveReference={onRemoveReference}
        />
      </InputContextProvider>,
    );

    const shelfList = container.querySelector(".composer > .input-reference-list--shelf");
    const richEditor = container.querySelector(".composer .composer-rich-editor") as HTMLDivElement | null;

    expect(shelfList).not.toBeNull();
    expect(shelfList?.textContent).toContain("notes.txt");
    expect(shelfList?.textContent).not.toContain("frontend-design");

    expect(richEditor).not.toBeNull();
    expect(richEditor?.textContent).toBe("先参考 frontend-design 再结合 上次对话 请继续优化");

    // Caret right after "history-ref-2" -> Backspace atomically removes that inline token
    const historyEndOffset = inlineDraft.indexOf("[@上次对话](ref:history-ref-2)") + "[@上次对话](ref:history-ref-2)".length;
    richEditor!.focus();
    setInlineEditorSelectionOffsets(richEditor!, historyEndOffset, historyEndOffset);
    fireEvent.keyDown(richEditor!, { key: "Backspace" });

    expect(onRemoveReference).toHaveBeenCalledWith("history-ref-2");
    expect(onInputChange).toHaveBeenCalledWith("先参考 [$frontend-design](ref:skill-ref-1) 再结合  请继续优化");

    // When only shelf references remain, native <textarea> renders and Backspace at 0 does not remove shelf cards
    onRemoveReference.mockClear();
    rerender(
      <InputContextProvider
        sessionId="session-1"
        workspaceRoot="/workspace"
        ready
        onAdd={vi.fn()}
        onSettings={vi.fn()}
      >
        <Composer
          {...defaultProps}
          input=""
          references={[composerReference]}
          onRemoveReference={onRemoveReference}
        />
      </InputContextProvider>,
    );
    const updatedTextarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(updatedTextarea.tagName).toBe("TEXTAREA");
    updatedTextarea.setSelectionRange(0, 0);
    fireEvent.keyDown(updatedTextarea, { key: "Backspace" });
    expect(onRemoveReference).not.toHaveBeenCalled();
  });

  it("inserts inline references at the active cursor position mid-sentence and opens preview on click", async () => {
    const lunaAgentRef: InputReference = {
      ...composerReference,
      id: "111122223333" + "0".repeat(52),
      kind: "agent",
      label: "luna-work",
      source: "luna-work",
      status: "selection",
    };
    const docxSkillRef: InputReference = {
      ...composerReference,
      id: "aabbccddeeff" + "9".repeat(52),
      kind: "skill",
      label: "docx",
      source: "skills/docx",
      status: "content",
    };

    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: {
        listSkills: vi.fn().mockResolvedValue({
          skills: [{
            qualifiedId: "skills/docx",
            name: "docx",
            description: "输出 Word 文档",
            sourceKind: "user",
            available: true,
            enabled: true,
          }],
        }),
        listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
        listMcpServers: vi.fn().mockResolvedValue({ servers: [] }),
        listSessions: vi.fn().mockResolvedValue({ items: [] }),
        listAgentTypes: vi.fn().mockResolvedValue({
          agents: [{
            name: "luna-work",
            source: "/workspace/.eidos/agents/luna-work.toml",
            description: "美食调研子智能体",
            sourceKind: "user",
            available: true,
            enabled: true,
          }],
        }),
        listWorkspaceDirectory: vi.fn().mockResolvedValue({ path: ".", entries: [], truncated: false }),
        prepareInput: vi.fn().mockImplementation(async (req: { kind: string }) =>
          req.kind === "agent" ? lunaAgentRef : docxSkillRef,
        ),
        readInput: vi.fn().mockResolvedValue({
          reference: docxSkillRef,
          text: "---\nname: docx\ndescription: 输出 Word 文档\n---\nGenerate structured docx files.",
        }),
        onInputQuote: vi.fn().mockReturnValue(vi.fn()),
      },
    });

    let latestText = "";
    function StatefulHarness() {
      const [text, setText] = useState("用这个帮我对比。并且输出文档。");
      const [refs, setRefs] = useState<InputReference[]>([]);
      const textRef = { current: text };
      const refsRef = { current: refs };
      latestText = text;
      const updateText = (next: string) => {
        textRef.current = next;
        setText(next);
      };
      const updateRefs = (next: InputReference[]) => {
        refsRef.current = next;
        setRefs(next);
      };
      return (
        <InputContextProvider
          sessionId="session-1"
          workspaceRoot="/workspace"
          ready
          onAdd={(_sid, ref) => updateRefs([...refsRef.current, ref])}
          onInsert={(_sid, ref, range) => {
            if (!isInlineReference(ref)) {
              updateRefs([...refsRef.current, ref]);
              return undefined;
            }
            const base = canonicalizeInlineText(textRef.current, refsRef.current);
            const inserted = insertInlineReferenceAtSelection(base, ref, range);
            updateText(inserted.text);
            updateRefs([...refsRef.current.filter((r) => r.id !== ref.id), ref]);
            return inserted.caretOffset;
          }}
          onSettings={vi.fn()}
        >
          <Composer
            {...defaultProps}
            input={text}
            references={refs}
            onInputChange={updateText}
            onRemoveReference={(id) => {
              updateText(removeInlineReferenceTokenById(textRef.current, id));
              updateRefs(refsRef.current.filter((r) => r.id !== id));
            }}
          />
        </InputContextProvider>
      );
    }

    const { container } = render(<StatefulHarness />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

    // 1. Place cursor at index 0 (start of sentence) and insert luna-work via "+" picker
    textarea.focus();
    textarea.setSelectionRange(0, 0);
    fireEvent.select(textarea);
    fireEvent.click(screen.getByRole("button", { name: "添加引用" }));
    fireEvent.click(await screen.findByRole("tab", { name: "智能体" }));
    fireEvent.click(await screen.findByRole("option", { name: /luna-work/ }));

    await waitFor(() => {
      expect(latestText).toBe("[@luna-work](ref:111122223333)用这个帮我对比。并且输出文档。");
    });

    // 2. Move cursor right after "对比。" inside InlineRichEditor and insert docx via "+" picker
    const richEditor = container.querySelector(".composer .composer-rich-editor") as HTMLDivElement;
    expect(richEditor).not.toBeNull();
    const midSentenceOffset = "[@luna-work](ref:111122223333)用这个帮我对比。".length;
    richEditor.focus();
    setInlineEditorSelectionOffsets(richEditor, midSentenceOffset, midSentenceOffset);
    fireEvent.click(richEditor);

    fireEvent.click(screen.getByRole("button", { name: "添加引用" }));
    fireEvent.click(await screen.findByRole("tab", { name: "Skill" }));
    fireEvent.click(await screen.findByRole("option", { name: /docx/ }));

    await waitFor(() => {
      expect(latestText).toBe(
        "[@luna-work](ref:111122223333)用这个帮我对比。[$docx](ref:aabbccddeeff)并且输出文档。",
      );
    });
    expect(richEditor.textContent).toBe("luna-work用这个帮我对比。docx并且输出文档。");

    // 3. Clicking the inline docx token opens SkillDetailDialog
    fireEvent.click(screen.getByRole("button", { name: "docx" }));
    const dialog = await screen.findByRole("dialog", { name: "docx" });
    expect(dialog).toHaveTextContent("Generate structured docx files.");
    fireEvent.click(screen.getByRole("button", { name: "关闭技能详情" }));
  });
});
