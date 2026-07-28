import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { SettingsPage } from "./settings/SettingsPage.js";
import { useModelController } from "../app/useModelController.js";
import type { EidosRuntimeAPI, ModelListResult, ModelStatus, RuntimeStatus } from "../contracts.js";

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
const mockUnconfiguredStatus: ModelStatus = {
  ...mockModelStatus,
  configured: false,
};

const mockModelList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [
    { id: "deepseek-v4-flash", provider: "deepseek", displayName: "Flash", configured: true, selectable: true },
  ],
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("SettingsPage DOM interaction & state behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
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

  it("Model Controller and UI own initial load, pending deduplication, and success", async () => {
    let resolveConfigure!: (status: ModelStatus) => void;
    const configureModel = vi.fn(() => new Promise<ModelStatus>((resolve) => {
      resolveConfigure = resolve;
    }));
    const listModels = vi.fn().mockResolvedValue(mockModelList);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      getModelStatus: vi.fn().mockResolvedValue(mockUnconfiguredStatus),
      listModels,
      configureModel,
    } as unknown as EidosRuntimeAPI;

    function Harness() {
      const [state, actions] = useModelController();
      useEffect(() => { void actions.load(); }, []);
      return (
        <SettingsPage
          {...defaultProps}
          extensionError="Extension load failed"
          model={state.status}
          modelList={state.list}
          modelLoading={state.loading}
          modelError={state.error}
          modelConfiguring={state.configuring}
          onConfigureModel={actions.configure}
        />
      );
    }

    render(<Harness />);
    expect(screen.getByText("正在从 Local Runtime 获取可用模型列表…")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Extension load failed");
    await screen.findByRole("button", { name: "配置 API Key" });

    fireEvent.click(screen.getByRole("button", { name: "配置 API Key" }));
    const input = screen.getByPlaceholderText("sk-…") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-controller-key-123456" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    expect(configureModel).toHaveBeenCalledTimes(1);
    expect(input).toHaveValue("sk-controller-key-123456");
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存中…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存中…" }));
    fireEvent.keyDown(input, { key: "Enter" });
    expect(configureModel).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveConfigure(mockModelStatus);
      await Promise.resolve();
    });

    expect(await screen.findByRole("button", { name: "更新凭证" })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("sk-…")).not.toBeInTheDocument();
    expect(screen.getAllByText("API Key 保存成功")).toHaveLength(1);
    expect(listModels).toHaveBeenCalledTimes(2);
  });

  it("Model Controller and UI keep safe failure state and complete one retry", async () => {
    const configureModel = vi.fn()
      .mockRejectedValueOnce(new Error("raw provider body: invalid credential"))
      .mockResolvedValueOnce(mockModelStatus);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      getModelStatus: vi.fn().mockResolvedValue(mockUnconfiguredStatus),
      listModels: vi.fn().mockResolvedValue(mockModelList),
      configureModel,
    } as unknown as EidosRuntimeAPI;

    function Harness() {
      const [state, actions] = useModelController();
      useEffect(() => { void actions.load(); }, []);
      return (
        <SettingsPage
          {...defaultProps}
          model={state.status}
          modelList={state.list}
          modelLoading={state.loading}
          modelError={state.error}
          modelConfiguring={state.configuring}
          onConfigureModel={actions.configure}
        />
      );
    }

    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: "配置 API Key" }));
    const input = screen.getByPlaceholderText("sk-…");
    fireEvent.change(input, { target: { value: "sk-invalid-key-123456" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    });

    expect(screen.getByRole("alert")).toHaveTextContent("操作失败，请查看 Runtime 日志。");
    expect(screen.queryByText(/raw provider body/)).not.toBeInTheDocument();
    expect(input).toHaveValue("sk-invalid-key-123456");
    expect(input).not.toBeDisabled();
    expect(screen.queryByText("API Key 保存成功")).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    });
    expect(configureModel).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("button", { name: "更新凭证" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
