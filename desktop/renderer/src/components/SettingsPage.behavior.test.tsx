import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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
    plugins: [],
    skills: [],
    mcpServers: [],
    extensionError: undefined,
    pendingAction: undefined,
    hasBlockingModal: false,
    onClose: vi.fn(),
    onConfigureModel: vi.fn().mockResolvedValue(undefined),
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
    const onConfigureSpy = vi.fn().mockImplementation(() => new Promise((r) => setTimeout(r, 100)));

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
});
