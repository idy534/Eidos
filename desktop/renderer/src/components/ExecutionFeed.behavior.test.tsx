import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Item, Run } from "../contracts.js";
import { ExecutionFeed } from "./ExecutionFeed.js";

const baseRun: Run = {
  id: "run-shell-feed",
  sessionId: "session-shell-feed",
  status: "running",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 1_000,
  startedAt: 1_000,
  updatedAt: 1_000,
};

function shellItem(overrides: Partial<Item> = {}): Item {
  return {
    id: "shell-item",
    sessionId: baseRun.sessionId,
    runId: baseRun.id,
    ordinal: 1,
    kind: "command_execution",
    status: "in_progress",
    createdAt: 1_000,
    content: "first line\n",
    toolCall: {
      id: "tool-shell-feed",
      itemId: "shell-item",
      modelStepIndex: 1,
      batchOrder: 0,
      providerCallId: "provider-shell-feed",
      toolName: "run_shell",
      status: "running",
      startedAt: 1_000,
      argumentsJson: JSON.stringify({ command: "pnpm test:fast" }),
    },
    ...overrides,
  };
}

function renderFeed(item: Item, run: Run = baseRun) {
  return render(
    <ExecutionFeed
      items={[item]}
      runs={[run]}
      approvals={[]}
      respondingApprovalIds={new Set()}
      respondingKindByApprovalId={{}}
      onApprove={() => {}}
      onReject={() => {}}
    />,
  );
}

describe("ExecutionFeed shell output", () => {
  it("updates from cumulative live output to a completed result without duplicating it", () => {
    const activeItem = shellItem();
    const { container, rerender } = renderFeed(activeItem);

    expect(screen.getByText("first line")).toBeInTheDocument();
    expect(screen.getByText("运行中")).toBeInTheDocument();

    const completedItem = shellItem({
      status: "completed",
      completedAt: 2_000,
      content: "first line\nsecond line\n",
      toolCall: {
        ...activeItem.toolCall!,
        status: "completed",
        completedAt: 2_000,
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Command completed",
          data: {
            stdout: "first line\nsecond line\n",
            stderr: "",
            exitCode: 0,
          },
        }),
      },
    });
    rerender(
      <ExecutionFeed
        items={[completedItem]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    const output = container.querySelector(".shell-output");
    expect(output?.textContent).toBe("first line\nsecond line\n");
    expect(container.querySelectorAll(".shell-output")).toHaveLength(1);
    expect(screen.getByText("✓ 成功")).toBeInTheDocument();
  });

  it("starts a shell details group collapsed and preserves manual expansion", () => {
    const { container, rerender } = renderFeed(shellItem());
    const detailsBefore = container.querySelector("details.tool-item--shell");
    expect(detailsBefore).not.toBeNull();
    expect(detailsBefore!.open).toBe(false);
    fireEvent.click(detailsBefore!.querySelector("summary")!);
    expect(detailsBefore!.open).toBe(true);

    rerender(
      <ExecutionFeed
        items={[shellItem({
          status: "completed",
          completedAt: 2_000,
          toolCall: {
            ...shellItem().toolCall!,
            status: "completed",
            completedAt: 2_000,
            resultJson: JSON.stringify({
              outcome: "success",
              code: "ok",
              summary: "Command completed",
              data: { stdout: "first line\n", stderr: "", exitCode: 0 },
            }),
          },
        })]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    const detailsAfter = container.querySelector("details.tool-item--shell");
    expect(detailsAfter).not.toBeNull();
    expect(detailsAfter!.open).toBe(true);
  });

  it("keeps a shell details group collapsed after the user closes it during streaming", () => {
    const { container, rerender } = renderFeed(shellItem());
    const detailsBefore = container.querySelector("details.tool-item--shell");
    expect(detailsBefore).not.toBeNull();
    fireEvent.click(detailsBefore!.querySelector("summary")!);
    expect(detailsBefore!.open).toBe(true);
    fireEvent.click(detailsBefore!.querySelector("summary")!);
    expect(detailsBefore!.open).toBe(false);

    rerender(
      <ExecutionFeed
        items={[shellItem({ content: "first line\nsecond line\n" })]}
        runs={[baseRun]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    const detailsDuring = container.querySelector("details.tool-item--shell");
    expect(detailsDuring).not.toBeNull();
    expect(detailsDuring!.open).toBe(false);

    rerender(
      <ExecutionFeed
        items={[shellItem({
          status: "completed",
          completedAt: 2_000,
          content: "first line\nsecond line\n",
          toolCall: {
            ...shellItem().toolCall!,
            status: "completed",
            completedAt: 2_000,
            resultJson: JSON.stringify({
              outcome: "success",
              code: "ok",
              summary: "Command completed",
              data: { stdout: "first line\nsecond line\n", stderr: "", exitCode: 0 },
            }),
          },
        })]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    const detailsAfter = container.querySelector("details.tool-item--shell");
    expect(detailsAfter).not.toBeNull();
    expect(detailsAfter!.open).toBe(false);
  });
});

describe("ExecutionFeed streaming follow", () => {
  function assistantItem(content: string): Item {
    return {
      id: "streaming-answer",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "assistant_message",
      status: "in_progress",
      createdAt: 1_000,
      content,
    };
  }

  function renderStreamingFeed(content: string) {
    return render(
      <ExecutionFeed
        items={[assistantItem(content)]}
        runs={[baseRun]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
  }

  function mockFeedMetrics(feed: HTMLElement, metrics: { scrollHeight: number; scrollTop: number; clientHeight: number }) {
    Object.defineProperty(feed, "scrollHeight", { value: metrics.scrollHeight, configurable: true });
    Object.defineProperty(feed, "clientHeight", { value: metrics.clientHeight, configurable: true });
    feed.scrollTop = metrics.scrollTop;
  }

  it("coalesces rapid deltas into a single frame follow while at the bottom", () => {
    const rafQueue: FrameRequestCallback[] = [];
    const requestSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      rafQueue.push(callback);
      return rafQueue.length;
    });
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    try {
      const { container, rerender } = renderStreamingFeed("hello");
      const feed = container.querySelector(".feed") as HTMLElement;
      mockFeedMetrics(feed, { scrollHeight: 1_000, scrollTop: 400, clientHeight: 600 });
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      expect(feed.scrollTop).toBe(1_000);
      requestSpy.mockClear();

      mockFeedMetrics(feed, { scrollHeight: 1_200, scrollTop: 1_000, clientHeight: 600 });
      rerender(
        <ExecutionFeed
          items={[assistantItem("hello wo")]}
          runs={[baseRun]}
          approvals={[]}
          respondingApprovalIds={new Set()}
          respondingKindByApprovalId={{}}
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );
      rerender(
        <ExecutionFeed
          items={[assistantItem("hello world")]}
          runs={[baseRun]}
          approvals={[]}
          respondingApprovalIds={new Set()}
          respondingKindByApprovalId={{}}
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );
      expect(requestSpy).toHaveBeenCalledTimes(1);

      mockFeedMetrics(feed, { scrollHeight: 1_200, scrollTop: 1_000, clientHeight: 600 });
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      expect(feed.scrollTop).toBe(1_200);
      expect(cancelSpy).not.toHaveBeenCalled();
    } finally {
      requestSpy.mockRestore();
      cancelSpy.mockRestore();
    }
  });

  it("does not yank the viewport after the user scrolls up", () => {
    const rafQueue: FrameRequestCallback[] = [];
    const requestSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      rafQueue.push(callback);
      return rafQueue.length;
    });
    try {
      const { container, rerender } = renderStreamingFeed("hello");
      const feed = container.querySelector(".feed") as HTMLElement;
      mockFeedMetrics(feed, { scrollHeight: 1_000, scrollTop: 400, clientHeight: 600 });
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      requestSpy.mockClear();

      mockFeedMetrics(feed, { scrollHeight: 2_000, scrollTop: 0, clientHeight: 600 });
      fireEvent.scroll(feed);
      expect(screen.getByRole("button", { name: "滚动到最新内容" })).not.toHaveAttribute("hidden");

      mockFeedMetrics(feed, { scrollHeight: 2_200, scrollTop: 0, clientHeight: 600 });
      rerender(
        <ExecutionFeed
          items={[assistantItem("hello world, still streaming")]}
          runs={[baseRun]}
          approvals={[]}
          respondingApprovalIds={new Set()}
          respondingKindByApprovalId={{}}
          onApprove={() => {}}
          onReject={() => {}}
        />,
      );
      expect(requestSpy).not.toHaveBeenCalled();
      while (rafQueue.length > 0) rafQueue.shift()!(16);
      expect(feed.scrollTop).toBe(0);
    } finally {
      requestSpy.mockRestore();
    }
  });

  it("cancels a pending follow when unmounted", () => {
    const rafQueue: FrameRequestCallback[] = [];
    const requestSpy = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      rafQueue.push(callback);
      return rafQueue.length;
    });
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {
      rafQueue.length = 0;
    });
    try {
      const { unmount } = renderStreamingFeed("hello");
      expect(requestSpy).toHaveBeenCalled();
      unmount();
      expect(cancelSpy).toHaveBeenCalled();
    } finally {
      requestSpy.mockRestore();
      cancelSpy.mockRestore();
    }
  });
});

describe("ExecutionFeed process group collapse behavior", () => {
  it("starts expanded while running and automatically collapses when transitioning to terminal status", () => {
    const activeItem = shellItem();
    const { container, rerender } = renderFeed(activeItem);

    const processGroup = container.querySelector("details.process-group");
    expect(processGroup).not.toBeNull();
    expect(processGroup).toHaveAttribute("open");
    expect(screen.getByText(/正在处理/)).toBeInTheDocument();

    const completedItem = shellItem({
      status: "completed",
      completedAt: 2_000,
      toolCall: {
        ...activeItem.toolCall!,
        status: "completed",
        completedAt: 2_000,
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Command completed",
          data: { stdout: "first line\n", stderr: "", exitCode: 0 },
        }),
      },
    });

    rerender(
      <ExecutionFeed
        items={[
          completedItem,
          {
            id: "final-response",
            sessionId: baseRun.sessionId,
            runId: baseRun.id,
            ordinal: 2,
            kind: "assistant_message",
            status: "completed",
            createdAt: 2_000,
            completedAt: 2_000,
            content: "这是工作结果",
          },
        ]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    // Process group should now be collapsed
    const processGroupAfter = container.querySelector("details.process-group");
    expect(processGroupAfter).not.toBeNull();
    expect(processGroupAfter).not.toHaveAttribute("open");
    expect(screen.getByText(/已处理/)).toBeInTheDocument();

    // Result should be visible
    expect(screen.getByText("这是工作结果")).toBeInTheDocument();

    // Clicking summary manually re-expands the process group
    fireEvent.click(processGroupAfter!.querySelector("summary")!);
    expect(processGroupAfter).toHaveAttribute("open");

    // Subsequent re-render preserves the user's manual expansion
    rerender(
      <ExecutionFeed
        items={[
          completedItem,
          {
            id: "final-response",
            sessionId: baseRun.sessionId,
            runId: baseRun.id,
            ordinal: 2,
            kind: "assistant_message",
            status: "completed",
            createdAt: 2_000,
            completedAt: 2_000,
            content: "这是工作结果",
          },
        ]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_500 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );
    expect(processGroupAfter).toHaveAttribute("open");
  });

  it("starts collapsed when loaded with an already-completed run", () => {
    const completedItem = shellItem({
      status: "completed",
      completedAt: 2_000,
      toolCall: {
        ...shellItem().toolCall!,
        status: "completed",
        completedAt: 2_000,
        resultJson: JSON.stringify({
          outcome: "success",
          code: "ok",
          summary: "Command completed",
          data: { stdout: "first line\n", stderr: "", exitCode: 0 },
        }),
      },
    });

    const { container } = render(
      <ExecutionFeed
        items={[completedItem]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000, updatedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    const processGroup = container.querySelector("details.process-group");
    expect(processGroup).not.toBeNull();
    expect(processGroup).not.toHaveAttribute("open");
    expect(screen.getByText(/已处理/)).toBeInTheDocument();
  });
});

describe("ExecutionFeed plan and clarification rendering", () => {
  function toolCallItem(toolName: string, overrides: Partial<Item> = {}, toolCallOverrides: Partial<ToolCall> = {}): Item {
    return {
      id: "tool-item-1",
      sessionId: baseRun.sessionId,
      runId: baseRun.id,
      ordinal: 1,
      kind: "command_execution",
      status: "in_progress",
      createdAt: 1_000,
      content: "",
      toolCall: {
        id: "tool-call-1",
        itemId: "tool-item-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "provider-call-1",
        toolName,
        status: "running",
        startedAt: 1_000,
        argumentsJson: "{}",
        ...toolCallOverrides,
      },
      ...overrides,
    };
  }

  it("renders active request_user_input in progress with question count and waiting prompt", () => {
    const item = toolCallItem(
      "request_user_input",
      { status: "in_progress" },
      {
        argumentsJson: JSON.stringify({
          questions: [
            { id: "q1", question: "第一题？" },
            { id: "q2", question: "第二题？" },
          ],
        }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[baseRun]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(screen.getByText("正在询问 2 个问题")).toBeInTheDocument();
    expect(screen.getByText("正在等待你的回答")).toBeInTheDocument();
  });

  it("renders completed request_user_input as collapsible summary with questions and answers", () => {
    const item = toolCallItem(
      "request_user_input",
      { status: "completed", completedAt: 2_000 },
      {
        status: "completed",
        completedAt: 2_000,
        argumentsJson: JSON.stringify({
          questions: [
            {
              id: "scope",
              question: "选择范围？",
              options: [
                { id: "frontend", label: "前端" },
                { id: "backend", label: "后端" },
              ],
            },
          ],
        }),
        resultJson: JSON.stringify({
          outcome: "success",
          data: {
            response: {
              status: "answered",
              answers: [
                {
                  questionId: "scope",
                  optionIds: ["frontend"],
                  text: "补充说明",
                },
              ],
            },
          },
        }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(screen.getByText("已询问 1 个问题")).toBeInTheDocument();
    expect(screen.getByText("选择范围？")).toBeInTheDocument();
    expect(screen.getByText("前端；补充说明")).toBeInTheDocument();
  });

  it("renders write_plan in progress with running indicator", () => {
    const item = toolCallItem(
      "write_plan",
      { status: "in_progress" },
      {
        argumentsJson: JSON.stringify({ title: "重构计划" }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[baseRun]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    expect(screen.getByText("正在制定计划…")).toBeInTheDocument();
  });

  it.each(["failed", "canceled", "declined", "completed"] as const)("shows an unsuccessful write_plan with %s item status as a tool result", (status) => {
    const item = toolCallItem("write_plan", {status}, {
      status: status === "declined" ? "failed" : status,
      argumentsJson: JSON.stringify({title: "Rejected plan"}),
      resultJson: JSON.stringify({outcome: "error", code: "plan_write_rejected", summary: "plan_not_found"}),
    });
    render(<ExecutionFeed items={[item]} runs={[baseRun]} approvals={[]}
      respondingApprovalIds={new Set()} respondingKindByApprovalId={{}}
      onApprove={() => {}} onReject={() => {}} />);
    expect(screen.queryByText("查看计划 →")).toBeNull();
    expect(screen.getByText("plan_not_found")).toBeInTheDocument();
  });

  it("renders completed write_plan card and invokes onOpenPlan when clicked", () => {
    const onOpenPlan = vi.fn();
    const item = toolCallItem(
      "write_plan",
      { status: "completed", completedAt: 2_000 },
      {
        status: "completed",
        completedAt: 2_000,
        argumentsJson: JSON.stringify({ title: "系统重构方案" }),
        resultJson: JSON.stringify({ outcome: "success" }),
      },
    );

    render(
      <ExecutionFeed
        items={[item]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
        onOpenPlan={onOpenPlan}
      />,
    );

    expect(screen.getByText("系统重构方案")).toBeInTheDocument();
    expect(screen.getByText("查看计划 →")).toBeInTheDocument();

    const planCard = screen.getByRole("button", { name: /系统重构方案/ });
    fireEvent.click(planCard);
    expect(onOpenPlan).toHaveBeenCalledTimes(1);
  });

  it("renders write_plan in the response area outside process group, even when other tools exist", () => {
    const readFileItem = toolCallItem(
      "read_file",
      { id: "read-1", ordinal: 1, status: "completed", completedAt: 1_500 },
      {
        id: "call-read-1",
        toolName: "read_file",
        status: "completed",
        argumentsJson: JSON.stringify({ path: "src/index.ts" }),
        resultJson: JSON.stringify({ outcome: "success", data: { content: "code" } }),
      },
    );

    const planItem = toolCallItem(
      "write_plan",
      { id: "plan-1", ordinal: 2, status: "completed", completedAt: 2_000 },
      {
        id: "call-plan-1",
        toolName: "write_plan",
        status: "completed",
        // Title in resultJson data (simulating fallback or snapshot)
        argumentsJson: "{}",
        resultJson: JSON.stringify({
          outcome: "success",
          data: { title: "从结果中读取的计划标题", planId: "p1" },
        }),
      },
    );

    const { container } = render(
      <ExecutionFeed
        items={[readFileItem, planItem]}
        runs={[{ ...baseRun, status: "succeeded", completedAt: 2_000 }]}
        approvals={[]}
        respondingApprovalIds={new Set()}
        respondingKindByApprovalId={{}}
        onApprove={() => {}}
        onReject={() => {}}
      />,
    );

    // Process group should contain read_file
    const processGroup = container.querySelector("details.process-group");
    expect(processGroup).not.toBeNull();
    expect(processGroup?.textContent).toContain("已读取");

    // Process group should NOT contain write_plan
    expect(processGroup?.querySelector(".tool-item--plan-card")).toBeNull();

    // write_plan should be in the response area outside process group
    const planCard = container.querySelector(".tool-item--plan-card");
    expect(planCard).not.toBeNull();
    expect(planCard?.textContent).toContain("从结果中读取的计划标题");
  });
});
