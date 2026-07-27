import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Composer, type ComposerProps } from "../app/AppShell.js";
import type { ModelListResult, Run } from "../contracts.js";

const mockModelList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [
    { id: "deepseek-v4-flash", provider: "deepseek", displayName: "Flash", configured: true, selectable: true },
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
  hasRuns: false,
  cancelingRunId: undefined,
  onInputChange: vi.fn(),
  onSubmit: vi.fn(),
  onCancel: vi.fn(),
  onModelChange: vi.fn(),
};

describe("Composer DOM interaction & state behavior", () => {
  beforeEach(() => {
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

  it("Unconfigured model (modelConfigured=false) disables submit button", () => {
    render(<Composer {...defaultProps} modelConfigured={false} input="Valid task text" />);

    const submitBtn = screen.getByRole("button", { name: "开始" });
    expect(submitBtn).toBeDisabled();
    expect(screen.getByPlaceholderText("请先配置 DeepSeek API Key")).toBeInTheDocument();
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

  it("waiting_user_input presents Continue button and label", () => {
    render(
      <Composer
        {...defaultProps}
        composerMode="waiting_user_input"
        input="Supplemental details"
        submitKind="continue"
      />,
    );

    const submitBtn = screen.getByRole("button", { name: "继续中…" });
    expect(submitBtn).toBeInTheDocument();
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
});
