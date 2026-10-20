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

  it("renders the layout with page header, summary cards, and configuration sections", async () => {
    render(<WorktreeSettings />);

    // Should display the loaded header and subtitle
    expect(await screen.findByText(/基于独立 Git Worktree 为会话提供代码执行与分支隔离/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Worktrees" })).toBeInTheDocument();

    // Summary cards
    expect(screen.getByText("执行隔离机制")).toBeInTheDocument();
    expect(screen.getByText("Git Worktree (Detached HEAD)")).toBeInTheDocument();
    expect(screen.getByText("清理保留策略")).toBeInTheDocument();
    expect(screen.getByText("保留最近 15 个")).toBeInTheDocument();
    expect(screen.getByText("数据安全性保障")).toBeInTheDocument();

    // Section 1: 目录回收与保留配额
    expect(screen.getByRole("heading", { level: 2, name: "目录回收与保留配额" })).toBeInTheDocument();
    expect(screen.getByText("自动清理 managed Worktree")).toBeInTheDocument();
    expect(screen.getByText("保留最近的 Worktree 数量")).toBeInTheDocument();

    // Presets
    expect(screen.getByRole("button", { name: "15 个 (默认)" })).toBeInTheDocument();

    // Section 2: 工作机制与安全性保障
    expect(screen.getByRole("heading", { level: 2, name: "工作机制与安全性保障" })).toBeInTheDocument();
    expect(screen.getByText("自动快照保护（Worktree Snapshot）")).toBeInTheDocument();
    expect(screen.getByText("会话与历史持久化（Durable Session）")).toBeInTheDocument();
    expect(screen.getByText("主代码库零污染")).toBeInTheDocument();
  });

  it("toggles automatic cleanup and triggers toast", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Worktrees" });

    const toggle = screen.getByRole("switch", { name: "自动清理 managed Worktree" });
    expect(toggle).toBeChecked();

    await user.click(toggle);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: false,
      managedWorktreeLimit: 15,
    });
    expect(onShowToast).toHaveBeenCalledWith("Worktree 设置已更新", "success");
  });

  it("increments limit using the stepper '+' button", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Worktrees" });

    const plusBtn = screen.getByRole("button", { name: "增加 1 个" });
    await user.click(plusBtn);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 16,
    });
    expect(onShowToast).toHaveBeenCalledWith("Worktree 设置已更新", "success");
  });

  it("decrements limit using the stepper '−' button", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Worktrees" });

    const minusBtn = screen.getByRole("button", { name: "减少 1 个" });
    await user.click(minusBtn);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 14,
    });
    expect(onShowToast).toHaveBeenCalledWith("Worktree 设置已更新", "success");
  });

  it("selects a preset chip", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Worktrees" });

    const chip30 = screen.getByRole("button", { name: "30 个" });
    await user.click(chip30);

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 30,
    });
    expect(onShowToast).toHaveBeenCalledWith("Worktree 设置已更新", "success");
  });

  it("handles manual input commit on Enter key", async () => {
    const user = userEvent.setup();
    const onShowToast = vi.fn();
    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Worktrees" });

    const input = screen.getByRole("textbox", { name: "保留最近的 Worktree 数量" });
    expect(input).toHaveValue("15");

    await user.clear(input);
    await user.type(input, "25{Enter}");

    expect(updateWorktreeSettingsMock).toHaveBeenCalledWith({
      automaticCleanup: true,
      managedWorktreeLimit: 25,
    });
    expect(onShowToast).toHaveBeenCalledWith("Worktree 设置已更新", "success");
  });

  it("clamps out-of-range input between 1 and 100", async () => {
    const user = userEvent.setup();
    render(<WorktreeSettings />);

    await screen.findByRole("heading", { level: 1, name: "Worktrees" });

    const input = screen.getByRole("textbox", { name: "保留最近的 Worktree 数量" });

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

    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取 Worktree 设置。");
    const retryBtn = screen.getByRole("button", { name: "重试" });
    expect(retryBtn).toBeInTheDocument();

    readWorktreeSettingsMock.mockResolvedValueOnce(initialSettings);
    await user.click(retryBtn);

    expect(await screen.findByRole("heading", { level: 2, name: "目录回收与保留配额" })).toBeInTheDocument();
  });

  it("displays error toast and error banner when saving fails", async () => {
    updateWorktreeSettingsMock.mockRejectedValueOnce(new Error("Save Error"));
    const user = userEvent.setup();
    const onShowToast = vi.fn();

    render(<WorktreeSettings onShowToast={onShowToast} />);

    await screen.findByRole("heading", { level: 1, name: "Worktrees" });

    const plusBtn = screen.getByRole("button", { name: "增加 1 个" });
    await user.click(plusBtn);

    expect(await screen.findByRole("alert")).toHaveTextContent("Worktree 设置保存失败。");
    expect(onShowToast).toHaveBeenCalledWith("Worktree 设置保存失败", "error");
  });
});
