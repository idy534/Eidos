import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { _resetResourceCacheForTest, ManagedResourceSettings } from "./ManagedResourceSettings.js";
import type { ResourceStatus } from "../../../../shared/managed-resources.js";

const original = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const status: ResourceStatus = {
  available: true, directory: "/Users/test/Eidos/Resource",
  packs: [
    { id: "document-python", title: "基础文档处理", version: "1", phase: "ready" },
    { id: "office", title: "Office 转换与重算", version: "1", phase: "downloading", downloadedBytes: 25, totalBytes: 100 },
    { id: "ocr", title: "OCR 引擎", version: "1", phase: "failed", errorCode: "resource_disk_full" },
  ],
};

afterEach(() => {
  cleanup();
  _resetResourceCacheForTest();
  vi.useRealTimers();
  if (original) Object.defineProperty(window, "eidosRuntime", original);
  else Reflect.deleteProperty(window, "eidosRuntime");
});

describe("managed document resources", () => {
  it("keeps readiness independent of Skill enablement and shows progress", async () => {
    const read = vi.fn().mockResolvedValue(status);
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { getResourceStatus: read } });
    render(<ManagedResourceSettings />);
    await userEvent.click(await screen.findByText("文档组件"));
    expect(screen.getByText("1/3 已就绪")).toBeVisible();
    expect(screen.getByText("下载中 25%")).toBeVisible();
    expect(screen.getByText("磁盘空间不足，请清理后重试。")).toBeVisible();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("retries only the requested resource and refreshes its state", async () => {
    const retry = vi.fn().mockResolvedValue({ accepted: true });
    const read = vi.fn().mockResolvedValue(status);
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { getResourceStatus: read, retryResource: retry } });
    render(<ManagedResourceSettings />);
    await userEvent.click(await screen.findByText("文档组件"));
    await userEvent.click(screen.getByRole("button", { name: "重试OCR 引擎" }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith({ packId: "ocr" }));
    expect(read.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("does not show the resource panel for a legacy Runtime", async () => {
    const read = vi.fn().mockResolvedValue({ available: false, directory: status.directory, packs: [] });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { getResourceStatus: read } });
    const { container } = render(<ManagedResourceSettings />);
    await waitFor(() => expect(read).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a failed status read without disabling other settings", async () => {
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: {
      getResourceStatus: vi.fn().mockRejectedValue(new Error("资源状态暂时不可用")),
    } });
    render(<ManagedResourceSettings />);
    await userEvent.click(await screen.findByText("文档组件"));
    expect(screen.getByRole("status")).toHaveTextContent("操作失败，请查看 Runtime 日志。");
  });

  it("stops polling when shutdown makes resources unavailable", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockResolvedValueOnce(status).mockResolvedValue({ available: false, directory: "", packs: [] });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { getResourceStatus: read } });
    const { container } = render(<ManagedResourceSettings />);
    await act(async () => {});
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(read).toHaveBeenCalledTimes(2);
    expect(container).toBeEmptyDOMElement();
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("does not rearm a pending poll after unmount", async () => {
    vi.useFakeTimers();
    const response = Promise.withResolvers<ResourceStatus>();
    const read = vi.fn().mockReturnValue(response.promise);
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { getResourceStatus: read } });
    const { unmount } = render(<ManagedResourceSettings />);
    unmount();
    await act(async () => { response.resolve(status); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("keeps a genuine read failure visible and retries on the next poll", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockRejectedValueOnce(new Error("unexpected Runtime failure")).mockResolvedValue(status);
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { getResourceStatus: read } });
    render(<ManagedResourceSettings />);
    await act(async () => {});
    expect(screen.getByText("操作失败，请查看 Runtime 日志。")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(read).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("操作失败，请查看 Runtime 日志。")).toBeNull();
    expect(screen.getByText("1/3 已就绪")).toBeInTheDocument();
  });

});
