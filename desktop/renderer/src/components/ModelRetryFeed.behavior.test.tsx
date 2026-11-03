import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ModelRetryActivity } from "../../../shared/context.generated.js";
import type { Item, Run } from "../contracts.js";
import { ExecutionFeed } from "./ExecutionFeed.js";
import { expandDetails } from "../../test/expand-details.js";

const run: Run = { id: "run", sessionId: "session", modelId: "fixture", status: "running", modelStepCount: 1, createdAt: 1, startedAt: 1, updatedAt: 4 };
const user: Item = { id: "user", ordinal: 0, createdAt: 1, kind: "user_message", content: "检查任务", runId: run.id, sessionId: run.sessionId, status: "completed" };
const waiting: ModelRetryActivity = {
  requestGroupId: "group", runId: run.id, eventId: 5, startedAt: 10, updatedAt: Date.now(), status: "waiting",
  retryIndex: 2, cause: "service_unavailable", httpStatus: 503, nextRetryAt: Date.now() + 8_000,
};
const props = { runs: [run], items: [user], approvals: [], onApprove() {}, onReject() {} };

describe("model retry stage in the feed", () => {
  it("replaces the thinking indicator with a live retry row and a countdown", () => {
    const { container } = render(<ExecutionFeed {...props} modelRetries={[waiting]} />);
    const row = container.querySelector(".model-retry")!;
    expect(row).toHaveAttribute("role", "status");
    expect(row).toHaveAttribute("data-phase", "waiting");
    expect(row.textContent).toContain("模型服务暂时不可用，正在重试");
    expect(row.textContent).toContain("第 2 次重试");
    expect(row.textContent).toContain("服务商返回 503");
    expect(row.textContent).toMatch(/[1-8] 秒后重试/);
    expect(screen.queryByText("正在思考")).not.toBeInTheDocument();
    expect(container.querySelector(".retry-pill")?.textContent).toBe("重试 1 次");
  });

  it("switches to the requesting phase in place and then keeps a quiet recovered row", async () => {
    const { container, rerender } = render(<ExecutionFeed {...props} modelRetries={[waiting]} />);
    const row = container.querySelector(".model-retry")!;
    rerender(<ExecutionFeed {...props} modelRetries={[{ ...waiting, status: "requesting", eventId: 6, nextRetryAt: null }]} />);
    expect(container.querySelector(".model-retry")).toBe(row);
    expect(row).toHaveAttribute("data-phase", "requesting");
    expect(row.textContent).toContain("正在第 2 次重试…");
    rerender(<ExecutionFeed {...props} runs={[{ ...run, status: "succeeded", completedAt: 30_000 }]}
      modelRetries={[{ ...waiting, status: "recovered", eventId: 7, nextRetryAt: null, updatedAt: 21_000 }]} />);
    await expandDetails(container, "details.process-group");
    expect(container.querySelectorAll(".model-retry")).toHaveLength(1);
    expect(container.querySelector(".model-retry")).toHaveAttribute("data-phase", "recovered");
    expect(container.querySelector(".model-retry")).not.toHaveAttribute("role");
    expect(container.querySelector(".retry-pill")).not.toHaveClass("retry-pill--failed");
  });

  it("shows a retry left behind by a canceled Run as canceled, not as still retrying", async () => {
    const { container } = render(<ExecutionFeed {...props} runs={[{ ...run, status: "canceled", completedAt: 20 }]} modelRetries={[waiting]} />);
    await expandDetails(container, "details.process-group");
    const row = container.querySelector(".model-retry")!;
    expect(row).toHaveAttribute("data-phase", "canceled");
    expect(row).toHaveAttribute("data-live", "false");
    expect(row.textContent).toContain("已取消");
  });

  it("reveals the details on demand", () => {
    const { container } = render(<ExecutionFeed {...props} modelRetries={[waiting]} />);
    expect(container.querySelector(".model-retry-detail")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "详情" }));
    expect(container.querySelector(".model-retry-detail")?.textContent).toContain("HTTP 状态503");
    expect(screen.getByRole("button", { name: "收起" })).toHaveAttribute("aria-expanded", "true");
  });

  it("replaces the one-line notice with a failure block that opens model settings", () => {
    const onOpenModelSettings = vi.fn();
    const failedRun: Run = { ...run, status: "failed", completedAt: 30_000, errorCode: "MODEL_AUTHENTICATION_FAILED",
      termination: { stage: "model", reason: "authentication_failed", httpStatus: 401 } };
    const { container } = render(<ExecutionFeed {...props} runs={[failedRun]} onOpenModelSettings={onOpenModelSettings} />);
    expect(container.querySelector(".run-notice")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("模型服务拒绝了 API Key");
    fireEvent.click(screen.getByRole("button", { name: "打开模型设置" }));
    expect(onOpenModelSettings).toHaveBeenCalledTimes(1);
  });

  it("keeps the ordinary notice for failures that are not provider errors", () => {
    const failedRun: Run = { ...run, status: "failed", completedAt: 30_000, errorCode: "INTERNAL_ERROR" };
    const { container } = render(<ExecutionFeed {...props} runs={[failedRun]} />);
    expect(container.querySelector(".run-notice--error")?.textContent).toBe("Eidos 内部错误导致任务失败");
    expect(container.querySelector(".model-failure")).toBeNull();
  });
});
