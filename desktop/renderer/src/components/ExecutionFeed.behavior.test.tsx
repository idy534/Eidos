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
