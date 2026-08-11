import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Session, SessionGitStatus } from "../contracts.js";
import { SessionSidebar } from "./SessionSidebar.js";


const managedSession: Session = {
  id: "session-managed",
  workspaceRoot: "/repository",
  worktree: {
    worktreeId: "worktree-a",
    projectId: "project-a",
    repositoryRoot: "/repository",
    worktreeRoot: "/runtime-data/worktrees/a",
    baseRef: "main",
    baseCommit: "a".repeat(40),
    branch: "eidos/a",
    state: "active",
  },
  title: "Managed Thread",
  taskStatus: "new",
  createdAt: 2,
  updatedAt: 2,
};

const dirtyStatus: SessionGitStatus = {
  worktreeId: "worktree-a",
  branch: "eidos/a",
  head: "b".repeat(40),
  baseRef: "main",
  baseCommit: "a".repeat(40),
  dirty: true,
  stagedCount: 0,
  unstagedCount: 1,
  untrackedCount: 0,
  conflictCount: 0,
  observedAt: 1,
};

describe("SessionSidebar Project and managed Thread behavior", () => {
  it("creates another Thread from the Project repository root", async () => {
    const user = userEvent.setup();
    const onCreateInProject = vi.fn();
    render(
      <SessionSidebar
        sessions={[managedSession]}
        selectedId={managedSession.id}
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        gitStatusBySessionId={new Map([[managedSession.id, dirtyStatus]])}
        onCreate={vi.fn()}
        onCreateInProject={onCreateInProject}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "在 repository 中新建任务" }));

    expect(onCreateInProject).toHaveBeenCalledWith("/repository");
    expect(onCreateInProject).not.toHaveBeenCalledWith("/runtime-data/worktrees/a");
  });

  it("shows the managed branch and a readable dirty indicator", () => {
    render(
      <SessionSidebar
        sessions={[managedSession]}
        selectedId={managedSession.id}
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        gitStatusBySessionId={new Map([[managedSession.id, dirtyStatus]])}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    expect(screen.getByText("eidos/a")).toBeInTheDocument();
    expect(screen.getByLabelText("有未提交改动")).toBeInTheDocument();
  });
});
