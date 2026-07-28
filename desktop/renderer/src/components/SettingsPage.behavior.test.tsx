import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPage } from "./settings/SettingsPage.js";
import type { ModelListResult, ModelStatus, RuntimeStatus } from "../contracts.js";

const mockRuntime: RuntimeStatus = {
  state: "ready",
  protocolVersion: 1,
  runtimeVersion: "0.3.0",
  storageHealth: { state: "ready" },
};

const mockModelStatus: ModelStatus = {
  provider: "deepseek",
  model: "deepseek-v4-flash",
  configured: true,
};

const mockModelList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [
    { id: "deepseek-v4-flash", provider: "deepseek", displayName: "Flash", configured: true, selectable: true },
  ],
};

describe("SettingsPage DOM interaction & state behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const defaultProps = {
    runtime: mockRuntime,
    model: mockModelStatus,
    modelList: mockModelList,
    modelLoading: false,
    modelError: undefined,
    modelConfiguring: false,
    plugins: [],
    skills: [],
    mcpServers: [],
    extensionError: undefined,
    pendingAction: undefined,
    hasBlockingModal: false,
    onClose: vi.fn(),
    onConfigureModel: vi.fn().mockResolvedValue(true),
    onImportPlugin: vi.fn().mockResolvedValue(undefined),
    onTogglePlugin: vi.fn().mockResolvedValue(undefined),
    onRemovePlugin: vi.fn().mockResolvedValue(undefined),
    onToggleMcp: vi.fn().mockResolvedValue(undefined),
  };

  it("Escape closes Settings when no nested dialog is open", async () => {
    const user = userEvent.setup();
    const onCloseSpy = vi.fn();

    render(<SettingsPage {...defaultProps} onClose={onCloseSpy} hasBlockingModal={false} />);

    await user.keyboard("{Escape}");
    expect(onCloseSpy).toHaveBeenCalledTimes(1);
  });

  it("Settings remains open on Escape while a nested dialog is active (hasBlockingModal=true)", async () => {
    const user = userEvent.setup();
    const onCloseSpy = vi.fn();

    render(<SettingsPage {...defaultProps} onClose={onCloseSpy} hasBlockingModal={true} />);

    await user.keyboard("{Escape}");
    expect(onCloseSpy).not.toHaveBeenCalled();
  });

  it("Extension error is rendered in alert banner", () => {
    render(<SettingsPage {...defaultProps} extensionError="Failed to load extension manifest" />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Failed to load extension manifest");
  });

  it("Model loading and pendingAction states are displayed in ModelSettings", async () => {
    const user = userEvent.setup();
    const onConfigureSpy = vi.fn().mockResolvedValue(true);

    render(
      <SettingsPage
        {...defaultProps}
        model={{ ...mockModelStatus, configured: false }}
        onConfigureModel={onConfigureSpy}
      />,
    );

    // Click "配置 API Key" button to open key edit form
    const configBtn = screen.getByRole("button", { name: "配置 API Key" });
    await user.click(configBtn);

    const input = screen.getByPlaceholderText("sk-…");
    await user.type(input, "sk-test-key-12345");

    const saveBtn = screen.getByRole("button", { name: "保存配置" });
    await user.click(saveBtn);

    expect(onConfigureSpy).toHaveBeenCalledWith("sk-test-key-12345");
  });

  it("Model configuration pending state disables input, save, cancel, and double submit", async () => {
    const onConfigureSpy = vi.fn().mockReturnValue(new Promise(() => {})); // unresolved promise

    const { rerender } = render(
      <SettingsPage
        {...defaultProps}
        model={{ ...mockModelStatus, configured: false }}
        modelConfiguring={false}
        onConfigureModel={onConfigureSpy}
      />,
    );

    // Open edit mode
    const configBtn = screen.getByRole("button", { name: "配置 API Key" });
    fireEvent.click(configBtn);

    const input = screen.getByPlaceholderText("sk-…") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-pending-key-123456" } });

    // Submit configuration (length >= 16)
    const saveBtn = screen.getByRole("button", { name: "保存配置" });
    fireEvent.click(saveBtn);
    expect(onConfigureSpy).toHaveBeenCalledTimes(1);

    // Update modelConfiguring prop to true (simulating controller state while unresolved)
    rerender(
      <SettingsPage
        {...defaultProps}
        model={{ ...mockModelStatus, configured: false }}
        modelConfiguring={true}
        onConfigureModel={onConfigureSpy}
      />,
    );

    expect(input).toBeDisabled();

    const cancelBtn = screen.getByRole("button", { name: "取消" });
    expect(cancelBtn).toBeDisabled();

    // 2nd click does not issue 2nd IPC request
    fireEvent.click(saveBtn);
    expect(onConfigureSpy).toHaveBeenCalledTimes(1);
  });

  it("Model configuration failure preserves input, keeps edit mode open, and re-enables controls for retry", async () => {
    const onConfigureSpy = vi.fn().mockResolvedValue(false);

    render(
      <SettingsPage
        {...defaultProps}
        model={{ ...mockModelStatus, configured: false }}
        modelConfiguring={false}
        modelError="API Key 无效"
        onConfigureModel={onConfigureSpy}
      />,
    );

    // Open edit mode
    const configBtn = screen.getByRole("button", { name: "配置 API Key" });
    fireEvent.click(configBtn);

    const inputAfter = screen.getByPlaceholderText("sk-…");
    expect(inputAfter).not.toBeDisabled();

    const cancelBtn = screen.getByRole("button", { name: "取消" });
    expect(cancelBtn).not.toBeDisabled();

    expect(screen.getByRole("alert")).toHaveTextContent("API Key 无效");
  });

  it("Integrates with useModelController: configure failure sets modelError and returns false safely without crashing", async () => {
    const { useModelController } = await import("../app/useModelController.js");
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      getModelStatus: vi.fn().mockResolvedValue(mockModelStatus),
      listModels: vi.fn().mockResolvedValue(mockModelList),
      configureModel: vi.fn().mockRejectedValue(new Error("API Key verification failed")),
    } as unknown as EidosRuntimeAPI;

    function TestHarness() {
      const [state, actions] = useModelController();
      return (
        <div>
          <span data-testid="model-error">{state.error ?? "no-error"}</span>
          <SettingsPage
            {...defaultProps}
            model={state.status ?? { provider: "deepseek", model: "v4", configured: false }}
            modelList={state.list ?? mockModelList}
            modelLoading={state.loading}
            modelError={state.error}
            modelConfiguring={state.configuring}
            onConfigureModel={(key) => actions.configure(key)}
          />
        </div>
      );
    }

    render(<TestHarness />);

    const configBtn = screen.getByRole("button", { name: "配置 API Key" });
    fireEvent.click(configBtn);

    const input = screen.getByPlaceholderText("sk-…");
    fireEvent.change(input, { target: { value: "sk-invalid-key-12345" } });

    const saveBtn = screen.getByRole("button", { name: "保存配置" });

    await act(async () => {
      fireEvent.click(saveBtn);
    });

    expect(screen.getByTestId("model-error")).toHaveTextContent("操作失败，请查看 Runtime 日志。");
  });
});
