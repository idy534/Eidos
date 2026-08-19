import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Session, SessionGitStatus } from "../contracts.js";
import { SessionSidebar } from "./SessionSidebar.js";


const managedSession: Session = {
  id: "session-managed",
  workspaceRoot: "/repository",
  project: {
    id: "project-a",
    workspaceRoot: "/repository",
    gitAvailable: true,
  },
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
  taskStatus: "completed",
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
  stagedFiles: [],
  unstagedFiles: ["README.md"],
  untrackedFiles: [],
  conflictFiles: [],
  observedAt: 1,
};

describe("SessionSidebar Project and managed Thread behavior", () => {
  it("creates another Thread from the Project repository root", async () => {
    const user = userEvent.setup();
    const onCreateInProject = vi.fn();
    render(
      <SessionSidebar
        sessions={[managedSession]}
        projects={[]}
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
        onDeleteProject={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "在 repository 中新建会话" }));

    expect(onCreateInProject).toHaveBeenCalledWith("/repository");
    expect(onCreateInProject).not.toHaveBeenCalledWith("/runtime-data/worktrees/a");
  });

  it("shows the managed branch and a readable dirty indicator", () => {
    render(
      <SessionSidebar
        sessions={[managedSession]}
        projects={[]}
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
        onDeleteProject={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    expect(screen.getByText("eidos/a")).toBeInTheDocument();
    expect(screen.getByLabelText("有未提交改动")).toBeInTheDocument();
  });

  it("groups a Direct Workspace without showing Git controls", () => {
    const directSession: Session = {
      ...managedSession,
      id: "session-direct",
      project: {
        id: "project-direct",
        workspaceRoot: "/report",
        gitAvailable: false,
      },
      workspaceRoot: "/report",
      worktree: undefined,
    };
    render(
      <SessionSidebar
        sessions={[directSession]}
        projects={[]}
        selectedId={directSession.id}
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        gitStatusBySessionId={new Map([[directSession.id, dirtyStatus]])}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onDeleteProject={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    expect(screen.queryByText("eidos/a")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("有未提交改动")).not.toBeInTheDocument();
  });

  it("shows an empty Project and allows deleting its metadata", async () => {
    const user = userEvent.setup();
    const project = {
      id: "project-empty",
      workspaceRoot: "/empty-project",
      gitAvailable: false,
      createdAt: 1,
      updatedAt: 1,
    };
    const onDeleteProject = vi.fn();
    render(
      <SessionSidebar
        sessions={[]}
        projects={[project]}
        selectedId={undefined}
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onDeleteProject={onDeleteProject}
        onOpenSettings={vi.fn()}
      />,
    );

    const region = screen.getByRole("region", { name: "empty-project" });
    const projectToggle = within(region).getByRole("button", { name: "empty-project" });
    fireEvent.contextMenu(projectToggle);

    const deleteItem = await screen.findByRole("menuitem", { name: "删除项目" });
    expect(deleteItem).toBeEnabled();
    await user.click(deleteItem);
    expect(onDeleteProject).toHaveBeenCalledWith(project);
  });

  it("does not show an empty Session as a task in the sidebar", () => {
    render(
      <SessionSidebar
        sessions={[{ ...managedSession, id: "empty-session", taskStatus: "new", title: undefined }]}
        projects={[]}
        selectedId="empty-session"
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onDeleteProject={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    expect(screen.queryByText("新任务")).not.toBeInTheDocument();
    expect(screen.getByText("还没有任务，点击上方按键创建")).toBeInTheDocument();
  });

  it("shows a recent conversation group without a project add button", () => {
    const projectlessSession: Session = {
      ...managedSession,
      id: "projectless-session",
      projectless: true,
      project: undefined,
      worktree: undefined,
      workspaceRoot: "/private/chat-workspaces/session",
      title: "闲聊",
    };
    render(
      <SessionSidebar
        sessions={[projectlessSession]}
        projects={[]}
        selectedId={projectlessSession.id}
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onDeleteProject={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    expect(screen.getByText("项目")).toBeInTheDocument();
    expect(screen.queryByText("项目与任务")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "最近" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "在 最近 中新建会话" })).not.toBeInTheDocument();
  });

  it("removes the leading project chevron and uses a trailing chevron for recent", async () => {
    const user = userEvent.setup();
    const projectlessSession: Session = {
      ...managedSession,
      id: "projectless-session-with-project",
      projectless: true,
      project: undefined,
      worktree: undefined,
      workspaceRoot: "/private/chat-workspaces/session",
      title: "闲聊",
    };
    render(
      <SessionSidebar
        sessions={[managedSession, projectlessSession]}
        projects={[]}
        selectedId={managedSession.id}
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onDeleteProject={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    const projectRegion = screen.getByRole("region", { name: "repository" });
    expect(projectRegion.querySelector(".sidebar-chevron")).not.toBeInTheDocument();
    expect(projectRegion.querySelector(".folder-icon")).toBeInTheDocument();

    const projectToggle = within(projectRegion).getByRole("button", { name: "repository" });
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");
    expect(projectToggle.querySelector(".folder-icon--open")).toBeInTheDocument();

    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "false");
    expect(projectToggle.querySelector(".folder-icon--open")).not.toBeInTheDocument();
    expect(screen.queryByText("Managed Thread")).not.toBeInTheDocument();

    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Managed Thread")).toBeInTheDocument();

    const recentRegion = screen.getByRole("region", { name: "最近" });
    const recentToggle = within(recentRegion).getByRole("button", { name: "最近" });
    expect(recentToggle.querySelector(".folder-icon")).not.toBeInTheDocument();
    expect(recentToggle.querySelector(".sidebar-chevron")).toBeInTheDocument();
    expect(recentToggle).toHaveAttribute("aria-expanded", "true");
    expect(recentToggle.querySelector(".sidebar-chevron")).toHaveClass("sidebar-chevron--open");

    await user.click(recentToggle);
    expect(recentToggle).toHaveAttribute("aria-expanded", "false");
    expect(recentToggle.querySelector(".sidebar-chevron")).not.toHaveClass("sidebar-chevron--open");
    expect(screen.queryByText("闲聊")).not.toBeInTheDocument();

    await user.click(recentToggle);
    expect(recentToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("闲聊")).toBeInTheDocument();
  });
});
