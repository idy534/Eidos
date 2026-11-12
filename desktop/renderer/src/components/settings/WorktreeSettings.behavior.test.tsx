import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI } from "../../contracts.js";
import type { WorktreeSettings as WorktreeSettingsValue } from "../../../../shared/domain-contracts.js";
import { WorktreeSettings } from "./WorktreeSettings.js";

const initialSettings: WorktreeSettingsValue = {
  automaticCleanup: true,
  managedWorktreeLimit: 15,
};

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("WorktreeSettings Component", () => {
  let readWorktreeSettingsMock: ReturnType<typeof vi.fn>;
  let updateWorktreeSettingsMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    readWorktreeSettingsMock = vi.fn().mockResolvedValue(structuredClone(initialSettings));
    updateWorktreeSettingsMock = vi.fn().mockImplementation(async (next: WorktreeSettingsValue) => ({
      ...next,
    }));

    const mockApi: Partial<EidosRuntimeAPI> = {
      readWorktreeSettings: readWorktreeSettingsMock,
      updateWorktreeSettings: updateWorktreeSettingsMock,
    };

    Object.defineProperty(window, "eidosRuntime", {
      value: mockApi as EidosRuntimeAPI,
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (runtimeDescriptor) {
      Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    } else {
      delete (window as Partial<Window>).eidosRuntime;
    }
  });

  it("renders only the header and the two cleanup controls", async () => {
    render(<WorktreeSettings />);

    expect(await screen.findByRole("heading", { level: 2, name: "清理策略" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Git 工作树" })).toBeInTheDocument();
    expect(screen.getByText("管理 Eidos 为会话创建的工作树目录。")).toBeInTheDocument();
    expect(screen.getByText("清理前保存改动，可在会话中恢复。")).toBeInTheDocument();

    expect(screen.getByRole("switch", { name: "自动清理旧工作树" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "保留上限" })).toHaveValue("15");
    expect(screen.getByRole("textbox", { name: "保留上限" })).toBeEnabled();

    // The page no longer repeats state in badges, summary cards, presets or explainer sections.
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(1);
    expect(screen.queryByRole("group", { name: "Worktree 数量预设" })).not.toBeInTheDocument();
    expect(screen.queryByText(/自动清理开启中/)).not.toBeInTheDocument();
    expect(screen.queryByText("工作机制与安全性保障")).not.toBeInTheDocument();
  });

  it("locks the limit while cleanup is off and unlocks it when cleanup is turned on", async () => {
    readWorktreeSettingsMock.mockResolvedValueOnce({ automaticCleanup: false, managedWorktreeLimit: 15 });
    const user = userEvent.setup();
    render(<WorktreeSettings />);

    const toggle = await screen.findByRole("switch", { name: "自动清理旧工作树" });
    expect(toggle).not.toBeChecked();
    expect(screen.getByRole("textbox", { name: "保留上限" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "增加 1 个" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "减少 1 个" })).toBeDisabled();

    await user.click(toggle);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({ automaticCleanup: true, managedWorktreeLimit: 15 });
    await waitFor(() => expect(screen.getByRole("textbox", { name: "保留上限" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "增加 1 个" })).toBeEnabled();
  });

  it("toggles automatic cleanup and triggers toast", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Git 工作树" });

    const toggle = screen.getByRole("switch", { name: "自动清理旧工作树" });
    expect(toggle).toBeChecked();

    await user.click(toggle);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: false,
      managedWorktreeLimit: 15,
    });
    expect(onShowToast).toHaveBeenCalledWith("工作树设置已更新", "success");
  });

  it("increments limit using the stepper '+' button", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Git 工作树" });

    const plusBtn = screen.getByRole("button", { name: "增加 1 个" });
    await user.click(plusBtn);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 16,
    });
    expect(onShowToast).toHaveBeenCalledWith("工作树设置已更新", "success");
  });

  it("decrements limit using the stepper '−' button", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Git 工作树" });

    const minusBtn = screen.getByRole("button", { name: "减少 1 个" });
    await user.click(minusBtn);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 14,
    });
    expect(onShowToast).toHaveBeenCalledWith("工作树设置已更新", "success");
  });

  it("handles manual input commit on Enter key", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Git 工作树" });

    const input = screen.getByRole("textbox", { name: "保留上限" });
    expect(input).toHaveValue("15");

    await user.clear(input);
    await user.type(input, "25{Enter}");

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 25,
    });
    expect(onShowToast).toHaveBeenCalledWith("工作树设置已更新", "success");
  });

  it("clamps out-of-range input between 1 and 100", async () => {
    const user = userEvent.setup();
    render(<WorktreeSettings />);

    await screen.findByRole("heading", { level: 1, name: "Git 工作树" });

    const input = screen.getByRole("textbox", { name: "保留上限" });

    await user.clear(input);
    await user.type(input, "999{Enter}");

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 100,
    });
  });

  it("displays error banner and retry button when loading fails", async () => {
    readWorktreeSettingsMock.mockRejectedValueOnce(new Error("Network Error"));
    const user = userEvent.setup();

    render(<WorktreeSettings />);

    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取工作树设置。");
    const retryBtn = screen.getByRole("button", { name: "重试" });
    expect(retryBtn).toBeInTheDocument();

    readWorktreeSettingsMock.mockResolvedValueOnce(initialSettings);
    await user.click(retryBtn);

    expect(await screen.findByRole("heading", { level: 2, name: "清理策略" })).toBeInTheDocument();
  });

  it("displays error toast and error banner when saving fails", async () => {
    updateWorktreeSettingsMock.mockRejectedValueOnce(new Error("Save Error"));
    const user = userEvent.setup();
    const onShowToast = vi.fn();

    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Git 工作树" });

    const plusBtn = screen.getByRole("button", { name: "增加 1 个" });
    await user.click(plusBtn);

    expect(await screen.findByRole("alert")).toHaveTextContent("工作树设置保存失败。");
    expect(onShowToast).toHaveBeenCalledWith("工作树设置保存失败", "error");
  });

  it("restores the saved limit in the input when a typed value fails to save", async () => {
    updateWorktreeSettingsMock.mockRejectedValueOnce(new Error("Save Error"));
    const user = userEvent.setup();
    render(<WorktreeSettings />);

    const input = await screen.findByRole("textbox", { name: "保留上限" });
    await user.clear(input);
    await user.type(input, "25{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("工作树设置保存失败。");
    await waitFor(() => expect(input).toHaveValue("15"));
  });
});
