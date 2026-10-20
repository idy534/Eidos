import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ContextCompactionActivity } from "../../../shared/context.generated.js";
import type { Item, Run } from "../contracts.js";
import { ExecutionFeed } from "./ExecutionFeed.js";

const run: Run = { id: "run", sessionId: "session", modelId: "fixture", status: "running", modelStepCount: 2, createdAt: 1, updatedAt: 4 };
const activity: ContextCompactionActivity = {
  requestId: "compact-one", runId: run.id, startedAt: 20, updatedAt: 20, eventId: 5, status: "running", trigger: "window_pressure",
};
const item = (id: string, ordinal: number, createdAt: number, kind: Item["kind"], content: string): Item => ({
  id, ordinal, createdAt, kind, content, runId: run.id, sessionId: run.sessionId, status: "completed",
});
const items = [item("user", 0, 1, "user_message", "检查任务"), item("before", 1, 10, "assistant_message", "先检查历史。"),
  { ...item("tool", 2, 30, "tool_call", ""), toolCall: {
    id: "read", itemId: "tool", modelStepIndex: 2, batchOrder: 0, providerCallId: "read", toolName: "read_file", status: "completed" as const,
    startedAt: 30, argumentsJson: '{"path":"after.md"}', resultJson: '{"outcome":"success"}',
  } }, item("answer", 3, 40, "assistant_message", "最终答复。")];
const props = { runs: [run], items, approvals: [], onApprove() {}, onReject() {} };

describe("context compaction in the process timeline", () => {
  it("inserts a static row at the start time and updates it in place", () => {
    const { container, rerender } = render(<ExecutionFeed {...props} contextCompactions={[activity]} />);
    const row = screen.getByText("上下文正在压缩").closest(".context-compaction")!;
    const process = container.querySelector(".process-content")!;
    expect(process.textContent?.indexOf("先检查历史。")).toBeLessThan(process.textContent!.indexOf("上下文正在压缩"));
    expect(process.textContent?.indexOf("上下文正在压缩")).toBeLessThan(process.textContent!.indexOf("after.md"));
    expect(row.querySelector("button, summary, details, [aria-expanded]")).toBeNull();
    expect(row).toHaveAttribute("role", "status");
    expect(container.querySelector(".feed-item--assistant")?.textContent).toContain("最终答复。");
    rerender(<ExecutionFeed {...props} contextCompactions={[{ ...activity, status: "succeeded", eventId: 7, updatedAt: 25 }]} />);
    expect(screen.getByText("上下文已自动压缩").closest(".context-compaction")).toBe(row);
    expect(container.querySelectorAll(".context-compaction")).toHaveLength(1);
  });

  it("preserves multiple automatic and manual compression points in order", () => {
    const first = { ...activity, status: "succeeded" as const };
    const second = { ...activity, requestId: "compact-two", startedAt: 35, eventId: 9, trigger: "manual" as const, status: "succeeded" as const };
    const { container } = render(<ExecutionFeed {...props} contextCompactions={[second, first]} />);
    expect([...container.querySelectorAll(".context-compaction")].map((row) => row.textContent))
      .toEqual(["上下文已自动压缩", "上下文已压缩"]);
    expect(container.querySelector(".feed-item--assistant")?.textContent).toContain("最终答复。");
  });

  it("shows pre-model compaction with no tools and leaves the final answer outside the process", () => {
    const { container } = render(<ExecutionFeed {...props} items={[items[0]!, items[3]!]} contextCompactions={[activity]} />);
    expect(screen.getByText("上下文正在压缩")).toBeInTheDocument();
    expect(container.querySelector(".process-content")?.textContent).not.toContain("最终答复。");
    expect(screen.queryByText("正在思考")).not.toBeInTheDocument();
  });

  it.each([
    ["failed", "running", "上下文压缩未完成", undefined],
    ["failed", "running", "上下文压缩已跳过", "no_compactable_history"],
    ["canceled", "canceled", "上下文压缩已取消", undefined],
    ["running", "interrupted", "上下文压缩已中断", undefined],
  ] as const)("shows %s activity in a %s Run truthfully", (status, runStatus, label, reason) => {
    const { container } = render(<ExecutionFeed {...props} runs={[{ ...run, status: runStatus }]}
      contextCompactions={[{ ...activity, status, ...(reason ? { reason } : {}) }]} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(container.querySelector(".context-compaction details")).toBeNull();
  });
});
