import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Composer, type ComposerProps } from "./Composer.js";
import type { ModelListResult, Run } from "../contracts.js";

const mockModelList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [
    {
      id: "deepseek-v4-flash", name: "DeepSeek-V4 Flash", vendor: "DeepSeek",
      provider: "deepseek", url: "https://api.deepseek.com/chat/completions",
      supportsToolCall: true, supportsImages: false, supportsReasoning: true,
      reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
    },
  ],
};

const defaultProps: ComposerProps = {
  composerMode: "idle",
  activeRun: undefined,
  input: "",
  modelList: mockModelList,
  selectedModelId: "deepseek-v4-flash",
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

describe("Composer DOM interaction & state behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("modelLoading disables textarea input and submit button", () => {
    render(<Composer {...defaultProps} modelLoading={true} input="Some text" />);

    const textarea = screen.getByPlaceholderText("正在加载模型配置…");
    const submitBtn = screen.getByRole("button", { name: "加载中…" });

    expect(textarea).toBeDisabled();
    expect(submitBtn).toBeDisabled();
  });

  it("Missing selectedModelId (undefined) disables submit button", () => {
    render(<Composer {...defaultProps} selectedModelId={undefined} input="Valid task text" />);

    const submitBtn = screen.getByRole("button", { name: "开始" });
    expect(submitBtn).toBeDisabled();
  });

  it("keeps the configured model selector available between completed Turns", () => {
    const onModelChange = vi.fn();
    render(<Composer {...defaultProps} onModelChange={onModelChange} />);

    fireEvent.change(screen.getByLabelText("本次模型"), { target: { value: "deepseek-v4-flash" } });
    expect(onModelChange).toHaveBeenCalledWith("deepseek-v4-flash");
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

  it("Read-only storage (composerMode='read_only') disables input and submit button", () => {
    render(<Composer {...defaultProps} composerMode="read_only" input="Valid task text" />);

    const textarea = screen.getByPlaceholderText("存储只读，暂无法启动 Run");
    const submitBtn = screen.getByRole("button", { name: "开始" });

    expect(textarea).toBeDisabled();
    expect(submitBtn).toBeDisabled();
  });

  it("waiting_approval and finalizing disable input and submit", () => {
    const { rerender } = render(<Composer {...defaultProps} composerMode="waiting_approval" input="Task text" />);

    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始" })).toBeDisabled();

    rerender(<Composer {...defaultProps} composerMode="finalizing" input="Task text" />);
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始" })).toBeDisabled();
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
        input="Task text"
      />,
    );

    expect(screen.getByRole("button", { name: "取消 Run" })).toBeInTheDocument();
    expect(screen.getByLabelText("本次模型")).toBeDisabled();

    const activeRunDisallowed: Run = {
      ...activeRunAllowed,
      allowedActions: [],
    };

    rerender(
      <Composer
        {...defaultProps}
        composerMode="running"
        activeRun={activeRunDisallowed}
        input="Task text"
      />,
    );

    expect(screen.queryByRole("button", { name: "取消 Run" })).not.toBeInTheDocument();
  });

  it("Empty input cannot submit", () => {
    render(<Composer {...defaultProps} input="   " />);

    const submitBtn = screen.getByRole("button", { name: "开始" });
    expect(submitBtn).toBeDisabled();
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
      <Composer {...defaultProps} composerMode="finalizing" input="" />,
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
      <Composer {...defaultProps} composerMode="finalizing" input="" />,
    );

    rerender(<Composer {...defaultProps} composerMode="idle" input="" />);
    expect(rafCallback).toBeDefined();

    unmount();
    expect(cancelSpy).toHaveBeenCalledWith(456);
  });

  it("auto-resizes textarea height bounded between min 56px and max 168px", () => {
    const { rerender } = render(<Composer {...defaultProps} input="Short text" />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

    // Simulate scrollHeight properties
    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 30 });
    rerender(<Composer {...defaultProps} input="Text step 1" />);
    expect(textarea.style.height).toBe("56px"); // clamped to minHeight 56px

    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 120 });
    rerender(<Composer {...defaultProps} input="Text step 2" />);
    expect(textarea.style.height).toBe("120px");

    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 250 });
    rerender(<Composer {...defaultProps} input="Very long multi line text" />);
    expect(textarea.style.height).toBe("168px"); // clamped to maxHeight 168px

    // Clearing input restores min height 56px
    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 20 });
    rerender(<Composer {...defaultProps} input="" />);
    expect(textarea.style.height).toBe("56px");
  });
});
