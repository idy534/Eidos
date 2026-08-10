import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SessionGitDiff, SessionGitStatus } from "../contracts.js";
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
  untrackedCount: 0,
  conflictCount: 0,
  observedAt: 1,
};

const diff: SessionGitDiff = {
  scope: "head",
  baseCommit: "a".repeat(40),
  head: "b".repeat(40),
  dirty: true,
  changedFiles: ["README.md", "src/index.ts"],
  unifiedDiff: "diff --git a/README.md b/README.md\n+updated\n",
  truncated: true,
  observedAt: 1,
};

describe("GitChangesPanel", () => {
  it("renders the default HEAD review as a read-only diff", () => {
    render(
      <GitChangesPanel
        scope="head"
        status={status}
        diff={diff}
        loading={false}
        error={undefined}
        onScopeChange={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByRole("tab", { name: "未提交改动" })).toHaveAttribute(
      "aria-selected", "true",
    );
    expect(screen.getByText("README.md")).toBeInTheDocument();
    expect(screen.getByText("src/index.ts")).toBeInTheDocument();
    expect(screen.getByText(/diff --git a\/README.md/)).toBeInTheDocument();
    expect(screen.getByText("Diff 已截断")).toBeInTheDocument();
    expect(screen.queryByText(/Stage|Discard|Commit/)).not.toBeInTheDocument();
  });

  it("exposes Baseline selection and manual refresh", () => {
    const onScopeChange = vi.fn();
    const onRefresh = vi.fn();
    render(
      <GitChangesPanel
        scope="head"
        status={status}
        diff={diff}
        loading={false}
        error={undefined}
        onScopeChange={onScopeChange}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "整个任务改动" }));
    fireEvent.click(screen.getByRole("button", { name: "刷新 Git 变更" }));

    expect(onScopeChange).toHaveBeenCalledWith("baseline");
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});
