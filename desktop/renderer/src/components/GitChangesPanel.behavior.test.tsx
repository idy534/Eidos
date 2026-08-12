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
    observedAt: 1,
  };
}

function renderPanel(overrides: Partial<Parameters<typeof GitChangesPanel>[0]> = {}) {
  const readDiff = vi.fn((_: string, __: string, path: string) => Promise.resolve(fileDiff(path)));
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

  it("groups structured status and requests only the selected file Diff", async () => {
    const { readDiff } = renderPanel();

    expect(screen.getByRole("region", { name: "Staged" })).toHaveTextContent("README.md");
    expect(screen.getByRole("region", { name: "Changes" })).toHaveTextContent("src/index.ts");
    expect(screen.getByRole("region", { name: "Untracked" })).toHaveTextContent("new file.txt");
    expect(screen.getByRole("region", { name: "Conflicts" })).toHaveTextContent("conflict.txt");
    await waitFor(() => expect(readDiff).toHaveBeenCalledWith(
      "session-a", "head", "README.md",
    ));

    fireEvent.click(screen.getByRole("button", { name: "src/index.ts" }));
    await waitFor(() => expect(readDiff).toHaveBeenLastCalledWith(
      "session-a", "head", "src/index.ts",
    ));
    expect(await screen.findByText("old")).toBeInTheDocument();
    expect(await screen.findByText("new")).toBeInTheDocument();
  });

  it("stages, unstages, discards, and opens the exact selected path", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { stage, unstage, discard, openInEditor, onRefresh } = renderPanel();
    await screen.findByText("old");

    fireEvent.click(screen.getByRole("button", { name: "Unstage" }));
    await waitFor(() => expect(unstage).toHaveBeenCalledWith(
      "session-a", ["README.md"], expect.any(String),
    ));

    fireEvent.click(screen.getByRole("button", { name: "src/index.ts" }));
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(stage).toHaveBeenCalledWith(
      "session-a", ["src/index.ts"], expect.any(String),
    ));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    await waitFor(() => expect(discard).toHaveBeenCalledWith(
      "session-a", "src/index.ts", expect.any(String),
    ));
    fireEvent.click(screen.getByRole("button", { name: "Open in Editor" }));
    await waitFor(() => expect(openInEditor).toHaveBeenCalledWith("session-a", "src/index.ts"));
    expect(onRefresh).toHaveBeenCalledTimes(3);
  });

  it("does not offer index or discard actions for a conflict", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "conflict.txt" }));

    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unstage" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open in Editor" })).toBeEnabled();
  });

  it("creates an inline comment from a Diff gutter and renders it as a widget", async () => {
    const { result, createComment } = renderPanel();
    await screen.findByText("new");
    const gutters = result.container.querySelectorAll(".diff-gutter");
    fireEvent.click(gutters[gutters.length - 1]!);
    fireEvent.change(screen.getByRole("textbox", { name: "Review comment" }), {
      target: { value: "Please add a test." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add Comment" }));

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
    renderPanel({ listComments, onSendReviewFeedback });

    expect(await screen.findByText("Add coverage.")).toBeInTheDocument();
    expect(await screen.findByText("Old feedback.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Send Review Feedback" }));

    await waitFor(() => expect(onSendReviewFeedback).toHaveBeenCalledWith(
      "Please address the following review feedback:\n"
      + "- README.md (new line 1): Add coverage.",
    ));
  });
});
