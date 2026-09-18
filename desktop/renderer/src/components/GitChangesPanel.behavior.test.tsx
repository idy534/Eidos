import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReviewComment, SessionGitDiff, SessionGitStatus } from "../contracts.js";
import { GitChangesPanel } from "./GitChangesPanel.js";


const status: SessionGitStatus = {
  worktreeId: "worktree-a",
  branch: "eidos/a",
  head: "b".repeat(40),
  baseRef: "main",
  baseCommit: "a".repeat(40),
  dirty: true,
  stagedCount: 1,
  unstagedCount: 1,
  untrackedCount: 1,
  conflictCount: 1,
  stagedFiles: ["README.md"],
  unstagedFiles: ["src/index.ts"],
  untrackedFiles: ["new file.txt"],
  conflictFiles: ["conflict.txt"],
  observedAt: 1,
};

function fileDiff(path: string): SessionGitDiff {
  return {
    scope: "head",
    compareRef: null,
    baseCommit: "a".repeat(40),
    head: "b".repeat(40),
    dirty: true,
    changedFiles: [path],
    unifiedDiff: [
      `diff --git a/${path} b/${path}`,
      `--- a/${path}`,
      `+++ b/${path}`,
      "@@ -1 +1 @@",
      "-old",
      "+new",
      "",
    ].join("\n"),
    diffHash: "d".repeat(64),
    truncated: false,
    additions: 1,
    deletions: 1,
    statsIncomplete: false,
    fileStats: [{ path, additions: 1, deletions: 1, statsIncomplete: false }],
    observedAt: 1,
  };
}

function summaryDiff(overrides: Partial<SessionGitDiff> = {}): SessionGitDiff {
  const paths = ["README.md", "src/index.ts", "new file.txt", "conflict.txt"];
  return {
    ...fileDiff("README.md"),
    changedFiles: paths,
    unifiedDiff: paths.map((path) => fileDiff(path).unifiedDiff).join("\n"),
    diffHash: "e".repeat(64),
    additions: paths.length,
    deletions: paths.length,
    fileStats: paths.map((path) => ({
      path,
      additions: 1,
      deletions: 1,
      statsIncomplete: false,
    })),
    ...overrides,
  };
}

function renderPanel(
  overrides: Partial<Parameters<typeof GitChangesPanel>[0]> = {},
  provideSummary = true,
) {
  const readDiff = vi.fn((_: string, __: string, path?: string) => (
    Promise.resolve(path ? fileDiff(path) : summaryDiff())
  ));
  const stage = vi.fn().mockResolvedValue(undefined);
  const unstage = vi.fn().mockResolvedValue(undefined);
  const discard = vi.fn().mockResolvedValue(undefined);
  const openInEditor = vi.fn().mockResolvedValue(undefined);
  const listComments = vi.fn().mockResolvedValue([]);
  const createComment = vi.fn().mockImplementation((sessionId, input) => Promise.resolve({
    id: input.commentId,
    sessionId,
    ...input,
    status: "active",
    createdAt: 1,
    updatedAt: 1,
  } satisfies ReviewComment));
  const deleteComment = vi.fn().mockResolvedValue("comment-a");
  const onRefresh = vi.fn();
  const result = render(
    <GitChangesPanel
      sessionId="session-a"
      workspaceRoot="/workspace"
      scope="head"
      status={status}
      loading={false}
      error={undefined}
      onScopeChange={vi.fn()}
      onRefresh={onRefresh}
      readDiff={readDiff}
      stage={stage}
      unstage={unstage}
      discard={discard}
      openInEditor={openInEditor}
      listComments={listComments}
      createComment={createComment}
      deleteComment={deleteComment}
      {...(provideSummary ? { summary: summaryDiff() } : {})}
      {...overrides}
    />,
  );
  return {
    result, readDiff, stage, unstage, discard, openInEditor, onRefresh,
    listComments, createComment, deleteComment,
  };
}

describe("GitChangesPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders all changed files as collapsed accordions and requests only expanded file Diffs", async () => {
    const { readDiff } = renderPanel();

    expect(screen.getByRole("region", { name: "已暂存" })).toHaveTextContent("README.md");
    expect(screen.getByRole("region", { name: "修改" })).toHaveTextContent("src/index.ts");
    expect(screen.getByRole("region", { name: "未跟踪" })).toHaveTextContent("new file.txt");
    expect(screen.getByRole("region", { name: "冲突" })).toHaveTextContent("conflict.txt");
    expect(screen.getByRole("button", { name: /README\.md/ })).toHaveAttribute(
      "aria-expanded", "false",
    );
    expect(screen.getByRole("button", { name: /README\.md/ })).toHaveTextContent("+1");
    expect(screen.getByRole("button", { name: /README\.md/ })).toHaveTextContent("-1");
    expect(readDiff).not.toHaveBeenCalled();
    expect(screen.getByText("+4")).toBeInTheDocument();
    expect(screen.getByText("-4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /src\/index\.ts/ }));
    await waitFor(() => expect(readDiff).toHaveBeenLastCalledWith(
      "session-a", "head", "src/index.ts",
    ));
    expect(await screen.findByText("old")).toBeInTheDocument();
    expect(await screen.findByText("new")).toBeInTheDocument();
    expect(document.querySelector(".git-diff-unified")).toBeInTheDocument();
    expect(document.querySelector(".git-diff-unified .git-diff-line-numbers")).toBeInTheDocument();
  });

  it("loads one summary Diff when the controller has not supplied it", async () => {
    const { readDiff } = renderPanel({}, false);

    await waitFor(() => expect(readDiff).toHaveBeenCalledWith("session-a", "head"));
    expect(readDiff).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("+4")).toBeInTheDocument();
  });

  it("does not duplicate the controller summary request while its controlled value is loading", () => {
    const { readDiff } = renderPanel({ summary: undefined });

    expect(readDiff).not.toHaveBeenCalled();
  });

  it("expands and collapses every file Diff", async () => {
    const { readDiff } = renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "展开全部差异" }));
    await waitFor(() => expect(readDiff).toHaveBeenCalledTimes(4));
    for (const path of ["README.md", "src/index.ts", "new file.txt", "conflict.txt"]) {
      expect(screen.getByRole("button", { name: new RegExp(path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }))
        .toHaveAttribute("aria-expanded", "true");
    }

    fireEvent.click(screen.getByRole("button", { name: "折叠全部差异" }));
    expect(screen.getByRole("button", { name: /README\.md/ })).toHaveAttribute(
      "aria-expanded", "false",
    );
  });

  it("uses compact rounded glyphs for diff controls and file disclosure", () => {
    renderPanel();

    expect(screen.getByRole("button", { name: "展开全部差异" }).querySelector("path"))
      .toHaveAttribute("d", "M5 8V3m-2 2 2-2 2 2M5 12v5m-2-2 2 2 2-2M10 5h7M10 10h7M10 15h7");
    expect(screen.getByRole("button", { name: /README\.md/ }).querySelector(".git-file-disclosure-icon"))
      .toHaveAttribute("data-state", "closed");
  });

  it("loads expand-all file Diffs sequentially", async () => {
    const first = Promise.withResolvers<SessionGitDiff>();
    const readDiff = vi.fn((_: string, __: string, path?: string) => (
      path === "README.md" ? first.promise : Promise.resolve(fileDiff(path ?? "README.md"))
    ));
    renderPanel({ readDiff });

    fireEvent.click(screen.getByRole("button", { name: "展开全部差异" }));
    expect(readDiff).toHaveBeenCalledTimes(1);

    first.resolve(fileDiff("README.md"));
    await waitFor(() => expect(readDiff).toHaveBeenCalledTimes(4));
  });

  it("does not surface an incomplete-statistics hint when native stats report a binary file", () => {
    renderPanel({ summary: summaryDiff({ truncated: true, statsIncomplete: true }) });

    expect(screen.queryByText("统计不完整")).not.toBeInTheDocument();
    expect(screen.getByText("+4")).toBeInTheDocument();
  });

  it("keeps native statistics exact when only the rendered patch is truncated", () => {
    renderPanel({ summary: summaryDiff({ truncated: true, statsIncomplete: false }) });

    expect(screen.getByText("+4")).toBeInTheDocument();
    expect(screen.queryByText("统计不完整")).not.toBeInTheDocument();
  });

  it("shows entire task from session items instead of baseline changedFiles", async () => {
    const item = {
      id: "item-1",
      sessionId: "session-a",
      runId: "run-1",
      ordinal: 1,
      kind: "file_change",
      status: "completed",
      createdAt: 1,
      toolCall: {
        id: "tool-1",
        itemId: "item-1",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "provider-1",
        toolName: "apply_patch",
        status: "completed",
        startedAt: 1,
        completedAt: 2,
        changeDiff: [
          "diff --git a/task.ts b/task.ts",
          "--- a/task.ts",
          "+++ b/task.ts",
          "@@ -1 +1 @@",
          "-old",
          "+new",
          "",
        ].join("\n"),
        baseSha256: "b".repeat(64),
      },
    };
    renderPanel({
      scope: "head",
      items: [item],
      latestRunId: "run-1",
    } as Partial<Parameters<typeof GitChangesPanel>[0]>);

    fireEvent.click(screen.getByRole("button", { name: "Diff 范围" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "整个任务" }));

    expect(await screen.findByText("task.ts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Diff 范围" })).toHaveTextContent("整个任务");
    expect(screen.getByText("共 1 个文件修改")).toBeInTheDocument();
  });

  it("shows one useful empty state without a zero-file group", () => {
    const emptyStatus = {
      ...status,
      dirty: false,
      stagedCount: 0,
      unstagedCount: 0,
      untrackedCount: 0,
      conflictCount: 0,
      stagedFiles: [],
      unstagedFiles: [],
      untrackedFiles: [],
      conflictFiles: [],
    };
    renderPanel({
      scope: "head",
      status: emptyStatus,
      summary: summaryDiff({ scope: "head", changedFiles: [], additions: 0, deletions: 0 }),
    });

    expect(screen.queryByRole("region", { name: "整个任务" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("当前范围没有变更");
    expect(screen.getByRole("status")).toHaveTextContent("可以切换范围或刷新 Git 状态");
  });

  it("stages, unstages, discards, and opens the exact selected path", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { stage, unstage, discard, openInEditor, onRefresh } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /README\.md/ }));
    await screen.findByText("old");

    fireEvent.click(screen.getByRole("button", { name: "取消暂存" }));
    await waitFor(() => expect(unstage).toHaveBeenCalledWith(
      "session-a", ["README.md"], expect.any(String),
    ));

    fireEvent.click(screen.getByRole("button", { name: /README\.md/ }));
    fireEvent.click(screen.getByRole("button", { name: /src\/index\.ts/ }));
    fireEvent.click(screen.getByRole("button", { name: "暂存" }));
    await waitFor(() => expect(stage).toHaveBeenCalledWith(
      "session-a", ["src/index.ts"], expect.any(String),
    ));
    fireEvent.click(screen.getByRole("button", { name: "丢弃" }));
    await waitFor(() => expect(discard).toHaveBeenCalledWith(
      "session-a", "src/index.ts", expect.any(String),
    ));
    fireEvent.click(screen.getByRole("button", { name: "在编辑器中打开" }));
    await waitFor(() => expect(openInEditor).toHaveBeenCalledWith("session-a", "src/index.ts"));
    expect(onRefresh).toHaveBeenCalledTimes(3);
  });

  it("does not offer index or discard actions for a conflict", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /conflict\.txt/ }));

    expect(screen.queryByRole("button", { name: "暂存" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "取消暂存" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "丢弃" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "在编辑器中打开" })).toBeEnabled();
  });

  it("creates an inline comment from a Diff gutter and renders it as a widget", async () => {
    const { result, createComment } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /README\.md/ }));
    await screen.findByText("new");
    const gutters = result.container.querySelectorAll(".diff-gutter");
    fireEvent.click(gutters[gutters.length - 1]!);
    fireEvent.change(screen.getByRole("textbox", { name: "审阅评论" }), {
      target: { value: "Please add a test." },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加评论" }));

    await waitFor(() => expect(createComment).toHaveBeenCalledWith(
      "session-a",
      expect.objectContaining({
        path: "README.md",
        scope: "head",
        side: "new",
        line: 1,
        body: "Please add a test.",
        baseHead: "b".repeat(40),
        diffHash: "d".repeat(64),
      }),
      expect.any(String),
    ));
    expect(await screen.findByText("Please add a test.")).toBeInTheDocument();
  });

  it("sends only active comments through normal review feedback", async () => {
    const active: ReviewComment = {
      id: "comment-active",
      sessionId: "session-a",
      path: "README.md",
      scope: "head",
      side: "new",
      line: 1,
      body: "Add coverage.",
      baseHead: "b".repeat(40),
      diffHash: "d".repeat(64),
      status: "active",
      createdAt: 1,
      updatedAt: 1,
    };
    const stale: ReviewComment = {
      ...active,
      id: "comment-stale",
      body: "Old feedback.",
      status: "stale",
    };
    const listComments = vi.fn().mockResolvedValue([active, stale]);
    const onSendReviewFeedback = vi.fn().mockResolvedValue(undefined);
    renderPanel({ listComments, onSendReviewFeedback, expanded: true });

    fireEvent.click(screen.getByRole("button", { name: /README\.md/ }));
    expect(await screen.findByText("Add coverage.")).toBeInTheDocument();
    expect(await screen.findByText("Old feedback.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "发送审阅意见" }));

    await waitFor(() => expect(onSendReviewFeedback).toHaveBeenCalledWith(
      "Please address the following review feedback:\n"
      + "- README.md (new line 1): Add coverage.",
    ));
  });

  it("does not show review feedback button when not expanded, and shows it when expanded", () => {
    const onSendReviewFeedback = vi.fn();
    const first = renderPanel({ onSendReviewFeedback, expanded: false });
    expect(screen.queryByRole("button", { name: "发送审阅意见" })).not.toBeInTheDocument();
    first.result.unmount();

    renderPanel({ onSendReviewFeedback, expanded: true });
    expect(screen.getByRole("button", { name: "发送审阅意见" })).toBeInTheDocument();
  });

  it("renders single-row toolbar with scope dropdown and no compareRef line", () => {
    const onScopeChange = vi.fn();
    const first = renderPanel({ onScopeChange, expanded: false });

    expect(screen.getByRole("button", { name: "Diff 范围" })).toHaveTextContent("未提交");
    expect(document.querySelector(".git-scope-dropdown-label")).toHaveTextContent("未提交");
    expect(screen.queryByTitle(/eidos\/a →/)).not.toBeInTheDocument();
    expect(document.querySelector(".git-review-compare")).not.toBeInTheDocument();
    first.result.unmount();

    const second = renderPanel({ onScopeChange, expanded: true });
    expect(document.querySelector(".git-changes-panel--expanded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Diff 范围" })).toHaveTextContent("未提交");

    fireEvent.click(screen.getByRole("button", { name: "Diff 范围" }));
    expect(document.querySelector(".git-scope-dropdown-menu")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "整个任务" }));
    expect(screen.getByRole("button", { name: "Diff 范围" })).toHaveTextContent("整个任务");
    expect(onScopeChange).not.toHaveBeenCalled();
    second.result.unmount();
  });

  it("offers create branch in more options menu and excludes expand-all and refresh-git", () => {
    const onCreateBranch = vi.fn();
    renderPanel({ onCreateBranch, status: { ...status, dirty: false } });

    fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
    expect(screen.queryByRole("menuitem", { name: /全部差异/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "刷新 Git 变更" })).not.toBeInTheDocument();

    const createBranchItem = screen.getByRole("menuitem", { name: "创建分支..." });
    expect(createBranchItem).toBeInTheDocument();
    fireEvent.click(createBranchItem);
    expect(onCreateBranch).toHaveBeenCalledTimes(1);
  });
});
