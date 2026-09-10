import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
