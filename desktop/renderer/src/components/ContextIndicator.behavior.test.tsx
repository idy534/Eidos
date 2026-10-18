import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ContextIndicator } from "./ContextIndicator.js";
import type { ContextDetails } from "../../../shared/context-management.js";

const usage = { activeTokens: 12000, windowTokens: 128000, percentUsed: 9.4, source: "provider" as const };
const details: ContextDetails = {
  runId: "run-1", measurement: "provider", activeTokens: 12000, contextWindowTokens: 128000,
  sections: [{ name: "tool_results", tokens: 6000 }], manualPending: false,
  lastEstimationErrorPercent: -4.5, calibrationSamples: 3,
};

beforeEach(() => {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } },
    close: { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; this.dispatchEvent(new Event("close")); } },
  });
});
afterEach(() => vi.restoreAllMocks());

it("shows provider measurement, explicitly estimated shares, and queues manual compaction once", async () => {
  const readContextDetails = vi.fn().mockResolvedValue(details);
  const requestContextCompaction = vi.fn().mockResolvedValue({ requestId: "request-1", stage: "requested", trigger: "manual", status: "queued" });
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readContextDetails, requestContextCompaction } });
  render(<ContextIndicator usage={usage} runId="run-1" />);
  fireEvent.click(screen.getByRole("button", { name: "查看上下文详情" }));
  expect(await screen.findByRole("dialog", { name: "上下文详情" })).toBeInTheDocument();
  expect(await screen.findByText(/已使用 12K\/ 128K/)).toBeInTheDocument();
  expect(screen.getByText("工具结果")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "压缩上下文" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "准备开始压缩" })).toBeDisabled());
  expect(requestContextCompaction).toHaveBeenCalledExactlyOnceWith("run-1");
  fireEvent.click(screen.getByRole("button", { name: "关闭上下文详情" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("discards late details when switching runs and shows a concrete read failure", async () => {
  let resolve!: (value: ContextDetails) => void;
  const readContextDetails = vi.fn().mockReturnValueOnce(new Promise<ContextDetails>((done) => { resolve = done; })).mockRejectedValueOnce(new Error("offline"));
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readContextDetails } });
  const view = render(<ContextIndicator usage={usage} runId="run-1" />);
  fireEvent.click(screen.getByRole("button", { name: "查看上下文详情" }));
  view.rerender(<ContextIndicator usage={usage} runId="run-2" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("系统暂时无法读取上下文统计");
  resolve(details);
  await waitFor(() => expect(screen.queryByText(/已使用 12K\/ 128K/)).toBeNull());
});

it("keeps the manual action available after a request failure", async () => {
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: {
    readContextDetails: vi.fn().mockResolvedValue(details), requestContextCompaction: vi.fn().mockRejectedValue(new Error("busy")),
  } });
  render(<ContextIndicator usage={usage} runId="run-1" />);
  fireEvent.click(screen.getByRole("button", { name: "查看上下文详情" }));
  fireEvent.click(await screen.findByRole("button", { name: "压缩上下文" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("系统未能安排压缩");
  expect(screen.getByRole("button", { name: "压缩上下文" })).toBeEnabled();
});
