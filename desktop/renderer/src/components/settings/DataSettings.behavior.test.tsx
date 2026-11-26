import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataSettings } from "./DataSettings.js";
import { RuntimeSettings } from "./RuntimeSettings.js";
import { _resetResourceCacheForTest, ManagedResourceSettings } from "./ManagedResourceSettings.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const readyRuntime = {
  state: "ready" as const,
  runtimeVersion: "0.5.7",
  protocolVersion: 1 as const,
  runShell: true,
  modelConfigured: false,
  storageHealth: { state: "ready" as const },
};

afterEach(() => {
  cleanup();
  _resetResourceCacheForTest();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else Reflect.deleteProperty(window, "eidosRuntime");
  vi.restoreAllMocks();
});

describe("B3-3 Settings diagnostics, restore, restart and resource cleanup", () => {
  it("opens pre-restore confirmation dialog and restores data on confirm", async () => {
    const restoreData = vi.fn().mockResolvedValue(readyRuntime);
    const toast = vi.fn();
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: { backupData: vi.fn(), restoreData },
    });

    const user = userEvent.setup();
    render(<DataSettings onShowToast={toast} />);
    await user.click(screen.getByRole("button", { name: "恢复备份" }));
    expect(screen.getByRole("alertdialog")).toBeVisible();
    expect(screen.getByText(/自动回滚原数据目录/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "选择备份并恢复" }));
    await waitFor(() => expect(restoreData).toHaveBeenCalledOnce());
    expect(toast).toHaveBeenCalledWith("备份恢复已完成", "success");
  });

  it("triggers Runtime restart and redacted diagnostics export from RuntimeSettings", async () => {
    const restartRuntime = vi.fn().mockResolvedValue(readyRuntime);
    const exportDiagnostics = vi.fn().mockResolvedValue(true);
    const toast = vi.fn();
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        getResourceStatus: vi.fn().mockResolvedValue({ available: false, directory: "", packs: [] }),
        readShellEnvironment: vi.fn().mockResolvedValue({ revision: 0, policy: { inherit: "all" } }),
        readGuardrails: vi.fn().mockResolvedValue({ revision: 0, settings: { goal: {}, automation: {} } }),
        backupData: vi.fn(),
        restoreData: vi.fn(),
        restartRuntime,
        exportDiagnostics,
      },
    });

    const user = userEvent.setup();
    render(<RuntimeSettings runtime={readyRuntime} onShowToast={toast} />);
    await user.click(screen.getByRole("button", { name: "重启 Runtime" }));
    await waitFor(() => expect(restartRuntime).toHaveBeenCalledOnce());
    expect(toast).toHaveBeenCalledWith("Runtime 已重启并就绪", "success");

    await user.click(screen.getByRole("button", { name: "导出诊断包" }));
    await waitFor(() => expect(exportDiagnostics).toHaveBeenCalledOnce());
    expect(toast).toHaveBeenCalledWith("已导出脱敏诊断包", "success");
  });

  it("triggers legacy resource cleanup from ManagedResourceSettings", async () => {
    const cleanupResources = vi.fn().mockResolvedValue({ removedCount: 2, freedBytes: 2 * 1024 * 1024 });
    const getResourceStatus = vi.fn().mockResolvedValue({
      available: true,
      directory: "/Users/test/Eidos/Resource",
      packs: [{ id: "document-python", title: "基础文档处理", version: "1", phase: "ready" }],
    });
    const toast = vi.fn();
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: { getResourceStatus, cleanupResources },
    });

    const user = userEvent.setup();
    render(<ManagedResourceSettings onShowToast={toast} />);
    await user.click(await screen.findByText("文档组件"));
    await user.click(screen.getByRole("button", { name: "清理旧版缓存" }));
    await waitFor(() => expect(cleanupResources).toHaveBeenCalledOnce());
    expect(toast).toHaveBeenCalledWith("已清理 2 个旧版缓存（释放 2.0 MB）", "success");
  });
});
