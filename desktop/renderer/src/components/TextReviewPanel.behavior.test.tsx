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

    // Only 1 file in run-2, auto-expanded
    expect(screen.getByText("共 1 个文件修改")).toBeInTheDocument();
    const currentBtn = screen.getByRole("button", { name: "src/current.ts" });
    expect(currentBtn).toBeInTheDocument();
    expect(currentBtn).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByRole("button", { name: "src/old.ts" })).not.toBeInTheDocument();
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

    // Both files now visible in the list
    expect(screen.getByText("共 2 个文件修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/current.ts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "src/old.ts" })).toBeInTheDocument();
  });

  it("keeps '最近一轮修改' empty when the latest run has no patches", () => {
    const item1 = makeChangeItem({ id: "item-1", runId: "run-1", path: "src/old.ts" });

    render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[item1]}
          loading={false}
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    expect(screen.getByText("共 0 个文件修改")).toBeInTheDocument();
    expect(screen.getByText("本轮还没有已记录的文件补丁。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "src/old.ts" })).not.toBeInTheDocument();
  });

  it("groups headerless runtime diffs by the recorded result path", () => {
    const headerless = [
      "--- a/tests/sample_test.yaml",
      "+++ /dev/null",
      "@@ -1,2 +0,0 @@",
      "-old",
      "-gone",
      "",
    ].join("\n");
    const item: Item = {
      ...makeChangeItem({ id: "item-h", runId: "run-2", path: "src/ignored.ts", diffContent: headerless }),
      toolCall: {
        ...makeChangeItem({ id: "item-h", runId: "run-2", path: "src/ignored.ts", diffContent: headerless }).toolCall!,
        resultJson: JSON.stringify({ outcome: "success", code: "ok", data: { path: "tests/sample_test.yaml" } }),
      },
    };

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

    expect(screen.getByText("共 1 个文件修改")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "tests/sample_test.yaml" })).toBeInTheDocument();
  });

  it("expands and collapses individual files by clicking file headers", () => {
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

    expect(screen.getByText("共 2 个文件修改")).toBeInTheDocument();

    const aBtn = screen.getByRole("button", { name: "src/a.ts" });
    const bBtn = screen.getByRole("button", { name: "src/b.ts" });
    expect(aBtn).toHaveAttribute("aria-expanded", "false");
    expect(bBtn).toHaveAttribute("aria-expanded", "false");

    // Click to expand a.ts
    fireEvent.click(aBtn);
    expect(aBtn).toHaveAttribute("aria-expanded", "true");
    expect(bBtn).toHaveAttribute("aria-expanded", "false");

    // Click to collapse a.ts
    fireEvent.click(aBtn);
    expect(aBtn).toHaveAttribute("aria-expanded", "false");
  });

  it("expands and collapses all files with the toolbar toggle button", () => {
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

    const toggleAllBtn = screen.getByRole("button", { name: "展开全部差异" });
    expect(toggleAllBtn).toBeInTheDocument();

    // Click to expand all
    fireEvent.click(toggleAllBtn);
    expect(screen.getByRole("button", { name: "src/a.ts" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "src/b.ts" })).toHaveAttribute("aria-expanded", "true");

    // Now button should say '折叠全部差异'
    const collapseAllBtn = screen.getByRole("button", { name: "折叠全部差异" });
    expect(collapseAllBtn).toBeInTheDocument();

    // Click to collapse all
    fireEvent.click(collapseAllBtn);
    expect(screen.getByRole("button", { name: "src/a.ts" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "src/b.ts" })).toHaveAttribute("aria-expanded", "false");
  });

  it("calls onRefresh when the refresh button is clicked", () => {
    const onRefresh = vi.fn();
    const item = makeChangeItem({ id: "item-1", runId: "run-2", path: "src/a.ts" });

    render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[item]}
          loading={false}
          onRefresh={onRefresh}
          onFeedback={vi.fn().mockResolvedValue(undefined)}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    const refreshBtn = screen.getByRole("button", { name: "刷新修改记录" });
    fireEvent.click(refreshBtn);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("opens file in workspace when '在工作区打开' button is clicked", () => {
    const artifacts = artifactValue();
    const item = makeChangeItem({ id: "item-1", runId: "run-2", path: "src/a.ts" });

    render(
      <ArtifactProvider value={artifacts}>
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

    // Single file is auto-expanded
    const openBtn = screen.getByRole("button", { name: "在工作区打开 src/a.ts" });
    fireEvent.click(openBtn);
    expect(artifacts.openFile).toHaveBeenCalledWith("src/a.ts");
    expect(screen.getByRole("button", { name: "在编辑器中打开 src/a.ts" })).toBeInTheDocument();
  });

  it("submits line review feedback with anchor", async () => {
    const onFeedback = vi.fn().mockResolvedValue(undefined);
    const item = makeChangeItem({ id: "item-1", runId: "run-2", path: "src/a.ts" });

    const { container } = render(
      <ArtifactProvider value={artifactValue()}>
        <TextReviewPanel
          sessionId="session-a"
          runId="run-2"
          items={[item]}
          loading={false}
          onFeedback={onFeedback}
          disabled={false}
        />
      </ArtifactProvider>,
    );

    const gutter = container.querySelector(".diff-gutter");
    expect(gutter).not.toBeNull();
    fireEvent.click(gutter!);

    const input = screen.getByRole("textbox", { name: "本轮修改反馈" });
    expect(input).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "请优化此处的实现" } });

    fireEvent.click(screen.getByRole("button", { name: "发送反馈" }));
    expect(onFeedback).toHaveBeenCalledWith(expect.stringContaining("请优化此处的实现"));
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
