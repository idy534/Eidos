import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Item } from "../contracts.js";
import { ArtifactProvider } from "./ArtifactContext.js";
import { TextReviewPanel } from "./TextReviewPanel.js";

function makePatch(path: string, oldValue = "old", newValue = "new"): string {
  return [
    `diff --git a/${path} b/${path}`,
    `--- a/${path}`,
    `+++ b/${path}`,
    "@@ -1 +1 @@",
    `-${oldValue}`,
    `+${newValue}`,
    "",
  ].join("\n");
}

function makeChangeItem({
  id,
  runId = "run-2",
  path = "src/index.ts",
  diffContent,
}: {
  id: string;
  runId?: string;
  path?: string;
  diffContent?: string;
}): Item {
  return {
    id,
    sessionId: "session-a",
    runId,
    ordinal: 1,
    kind: "file_change",
    status: "completed",
    createdAt: 1,
    toolCall: {
      id: `tool-${id}`,
      itemId: id,
      modelStepIndex: 1,
      batchOrder: 0,
      providerCallId: `provider-${id}`,
      toolName: "apply_patch",
      status: "completed",
      startedAt: 1,
      completedAt: 2,
      changeDiff: diffContent ?? makePatch(path),
      baseSha256: "b".repeat(64),
    },
  };
}

function artifactValue() {
  return {
    sessionId: "session-a",
    executionRoot: "/workspace",
    openFile: vi.fn(),
    openBrowser: vi.fn(),
  };
}

describe("TextReviewPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("defaults to '最近一轮修改' and displays only current run changes", () => {
    const item1 = makeChangeItem({ id: "item-1", runId: "run-1", path: "src/old.ts" });
    const item2 = makeChangeItem({ id: "item-2", runId: "run-2", path: "src/current.ts" });

    render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[item1, item2]}
          loading={false}
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    const turnTab = screen.getByRole("tab", { name: "最近一轮修改" });
    const taskTab = screen.getByRole("tab", { name: "整个任务修改" });
    expect(turnTab).toHaveAttribute("aria-selected", "true");
    expect(taskTab).toHaveAttribute("aria-selected", "false");

    // Only 1 file in run-2
    expect(screen.getByText("共 1 个文件修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/current.ts" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "src/old.ts" })).not.toBeInTheDocument();

    // Does not show multi-file filter chips bar when only 1 file
    expect(screen.queryByRole("navigation", { name: "文件过滤" })).not.toBeInTheDocument();
  });

  it("switches to '整个任务修改' and displays all changes across the session", () => {
    const item1 = makeChangeItem({ id: "item-1", runId: "run-1", path: "src/old.ts" });
    const item2 = makeChangeItem({ id: "item-2", runId: "run-2", path: "src/current.ts" });

    render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[item1, item2]}
          loading={false}
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    fireEvent.click(screen.getByRole("tab", { name: "整个任务修改" }));

    const turnTab = screen.getByRole("tab", { name: "最近一轮修改" });
    const taskTab = screen.getByRole("tab", { name: "整个任务修改" });
    expect(turnTab).toHaveAttribute("aria-selected", "false");
    expect(taskTab).toHaveAttribute("aria-selected", "true");

    // Both files now visible
    expect(screen.getByText("共 2 个文件修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/current.ts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/old.ts" })).toBeInTheDocument();

    // Filter chips bar appears
    const nav = screen.getByRole("navigation", { name: "文件过滤" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部文件 (2)" })).toBeInTheDocument();
  });

  it("filters diffs by clicking file chips and restores by clicking '全部文件'", () => {
    const item1 = makeChangeItem({ id: "item-1", runId: "run-2", path: "src/a.ts" });
    const item2 = makeChangeItem({ id: "item-2", runId: "run-2", path: "src/b.ts" });

    render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[item1, item2]}
          loading={false}
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    // Initial state: both files shown
    expect(screen.getByText("共 2 个文件修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/a.ts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/b.ts" })).toBeInTheDocument();

    // Click chip for b.ts
    const bChip = screen.getByTitle("src/b.ts");
    fireEvent.click(bChip);

    // Only b.ts is displayed now
    expect(screen.queryByRole("button", { name: "src/a.ts" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/b.ts" })).toBeInTheDocument();

    // Click "全部文件 (2)" chip
    fireEvent.click(screen.getByRole("button", { name: "全部文件 (2)" }));
    expect(screen.getByRole("button", { name: "src/a.ts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/b.ts" })).toBeInTheDocument();
  });

  it("never renders disclaimer text or raw execution complete label", () => {
    const item = makeChangeItem({ id: "item-1", runId: "run-2", path: "src/index.ts" });

    render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[item]}
          loading={false}
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    expect(screen.queryByText(/这里按工具执行顺序展示文件修改记录/)).not.toBeInTheDocument();
    expect(screen.queryByText(/准备中的补丁不代表已经写入/)).not.toBeInTheDocument();
    expect(screen.queryByText(/只有路径而没有补丁的记录无法展示历史差异/)).not.toBeInTheDocument();
    expect(screen.queryByText("执行完成")).not.toBeInTheDocument();
  });

  it("displays loading and error states appropriately", () => {
    const { rerender } = render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[]}
          loading={true}
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在读取更早的修改记录…");

    rerender(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[]}
          loading={false}
          error="Network timeout"
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("修改记录尚未完整读取：Network timeout");
  });
});
