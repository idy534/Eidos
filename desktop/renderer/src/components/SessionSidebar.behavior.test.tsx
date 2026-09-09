import { beforeEach, describe, expect, it, vi } from "vitest";
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
  beforeEach(() => {
    window.localStorage.clear();
  });

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

  it("shows Reveal in Finder option in project context menu and triggers onShowInFinder", async () => {
    const user = userEvent.setup();
    const project = {
      id: "project-finder",
      workspaceRoot: "/finder-project",
      gitAvailable: false,
      createdAt: 1,
      updatedAt: 1,
    };
    const onShowInFinder = vi.fn();
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
        onDeleteProject={vi.fn()}
        onShowInFinder={onShowInFinder}
        onOpenSettings={vi.fn()}
      />,
    );

    const region = screen.getByRole("region", { name: "finder-project" });
    const projectToggle = within(region).getByRole("button", { name: "finder-project" });
    fireEvent.contextMenu(projectToggle);

    const finderItem = await screen.findByRole("menuitem", { name: "在 Finder 中显示" });
    expect(finderItem).toBeEnabled();
    await user.click(finderItem);
    expect(onShowInFinder).toHaveBeenCalledWith(project);
  });

  it("shows '暂无会话' placeholder when an empty project is expanded, and hides it when collapsed", async () => {
    const user = userEvent.setup();
    const project = {
      id: "project-empty-sessions",
      workspaceRoot: "/empty-sessions-project",
      gitAvailable: false,
      createdAt: 1,
      updatedAt: 1,
    };
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
        onDeleteProject={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    const region = screen.getByRole("region", { name: "empty-sessions-project" });
    const projectToggle = within(region).getByRole("button", { name: "empty-sessions-project" });

    // Initially expanded
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");
    const emptyNotice = screen.getByText("暂无会话");
    expect(emptyNotice).toBeVisible();

    // Collapse
    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "false");
    expect(emptyNotice).not.toBeVisible();

    // Re-expand
    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");
    expect(emptyNotice).toBeVisible();
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
    expect(screen.getByText("Managed Thread")).not.toBeVisible();

    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Managed Thread")).toBeVisible();

    const recentRegion = screen.getByRole("region", { name: "最近" });
    const recentToggle = within(recentRegion).getByRole("button", { name: "最近" });
    expect(recentToggle.querySelector(".folder-icon")).not.toBeInTheDocument();
    expect(recentToggle.querySelector(".sidebar-chevron")).toBeInTheDocument();
    expect(recentToggle).toHaveAttribute("aria-expanded", "true");
    expect(recentToggle.querySelector(".sidebar-chevron")).toHaveClass("sidebar-chevron--open");

    await user.click(recentToggle);
    expect(recentToggle).toHaveAttribute("aria-expanded", "false");
    expect(recentToggle.querySelector(".sidebar-chevron")).not.toHaveClass("sidebar-chevron--open");
    expect(screen.getByText("闲聊")).not.toBeVisible();

    await user.click(recentToggle);
    expect(recentToggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("闲聊")).toBeVisible();
  });

  it("defaults to showing 6 sessions and shows '展开全部' button when project session count > 6, then collapses and resets", async () => {
    const user = userEvent.setup();
    const sessions: Session[] = Array.from({ length: 8 }, (_, index) => ({
      ...managedSession,
      id: `session-${index + 1}`,
      title: `任务 ${index + 1}`,
      createdAt: 1000 + index,
    }));

    render(
      <SessionSidebar
        sessions={sessions}
        projects={[]}
        selectedId={sessions[0].id}
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

    // sessions are sorted descending by createdAt: 任务 8, 任务 7, 任务 6, 任务 5, 任务 4, 任务 3
    expect(screen.getByText("任务 8")).toBeInTheDocument();
    expect(screen.getByText("任务 7")).toBeInTheDocument();
    expect(screen.getByText("任务 6")).toBeInTheDocument();
    expect(screen.getByText("任务 5")).toBeInTheDocument();
    expect(screen.getByText("任务 4")).toBeInTheDocument();
    expect(screen.getByText("任务 3")).toBeInTheDocument();
    expect(screen.queryByText("任务 2")).not.toBeInTheDocument();
    expect(screen.queryByText("任务 1")).not.toBeInTheDocument();

    const expandAllButton = screen.getByRole("button", { name: "展开全部" });
    expect(expandAllButton).toBeInTheDocument();

    // Click "展开全部"
    await user.click(expandAllButton);

    // All sessions should be displayed, button disappears
    expect(screen.getByText("任务 2")).toBeInTheDocument();
    expect(screen.getByText("任务 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开全部" })).not.toBeInTheDocument();

    // Collapse the project
    const projectToggle = screen.getByRole("button", { name: "repository" });
    await user.click(projectToggle);

    // Re-open the project
    await user.click(projectToggle);

    // Should reset to showing top 6 and "展开全部" button reappears
    expect(screen.getByText("任务 8")).toBeInTheDocument();
    expect(screen.getByText("任务 3")).toBeInTheDocument();
    expect(screen.queryByText("任务 2")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开全部" })).toBeInTheDocument();
  });

  it("does not show '展开全部' button when project has 6 or fewer sessions", () => {
    const sessions: Session[] = Array.from({ length: 6 }, (_, index) => ({
      ...managedSession,
      id: `session-${index + 1}`,
      title: `任务 ${index + 1}`,
      createdAt: 1000 + index,
    }));

    render(
      <SessionSidebar
        sessions={sessions}
        projects={[]}
        selectedId={sessions[0].id}
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

    for (let i = 1; i <= 6; i++) {
      expect(screen.getByText(`任务 ${i}`)).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "展开全部" })).not.toBeInTheDocument();
  });

  it("supports '展开全部' and collapse-reset on projectless ('最近') sessions", async () => {
    const user = userEvent.setup();
    const sessions: Session[] = Array.from({ length: 8 }, (_, index) => ({
      ...managedSession,
      id: `recent-session-${index + 1}`,
      projectless: true,
      project: undefined,
      worktree: undefined,
      workspaceRoot: "/private/chat-workspaces/session",
      title: `最近任务 ${index + 1}`,
      createdAt: 1000 + index,
    }));

    render(
      <SessionSidebar
        sessions={sessions}
        projects={[]}
        selectedId={sessions[0].id}
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

    expect(screen.getByText("最近任务 8")).toBeInTheDocument();
    expect(screen.getByText("最近任务 3")).toBeInTheDocument();
    expect(screen.queryByText("最近任务 2")).not.toBeInTheDocument();
    expect(screen.queryByText("最近任务 1")).not.toBeInTheDocument();

    const expandAllButton = screen.getByRole("button", { name: "展开全部" });
    await user.click(expandAllButton);

    expect(screen.getByText("最近任务 1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开全部" })).not.toBeInTheDocument();

    const recentToggle = screen.getByRole("button", { name: "最近" });
    await user.click(recentToggle);
    await user.click(recentToggle);

    expect(screen.getByText("最近任务 8")).toBeInTheDocument();
    expect(screen.queryByText("最近任务 2")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "展开全部" })).toBeInTheDocument();
  });

  it("remembers collapsed and expanded project state in localStorage across remounts", async () => {
    const user = userEvent.setup();
    const props = {
      sessions: [managedSession],
      projects: [],
      selectedId: undefined,
      disabled: false,
      readCompletedSessions: new Set<string>(),
      runtimePresentation: { tone: "success", label: "Ready" } as const,
      onCreate: vi.fn(),
      onCreateInProject: vi.fn(),
      onSelect: vi.fn(),
      onRename: vi.fn(),
      onDelete: vi.fn(),
      onDeleteProject: vi.fn(),
      onOpenSettings: vi.fn(),
    };

    const { unmount } = render(<SessionSidebar {...props} />);

    const projectToggle = screen.getByRole("button", { name: "repository" });
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");

    // Click to collapse
    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "false");

    // Unmount and remount SessionSidebar
    unmount();
    const { unmount: unmount2 } = render(<SessionSidebar {...props} />);

    const projectToggleAfterRemount = screen.getByRole("button", { name: "repository" });
    expect(projectToggleAfterRemount).toHaveAttribute("aria-expanded", "false");

    // Click to expand again
    await user.click(projectToggleAfterRemount);
    expect(projectToggleAfterRemount).toHaveAttribute("aria-expanded", "true");

    // Remount again to verify it stays expanded
    unmount2();
    render(<SessionSidebar {...props} />);
    const projectToggleFinal = screen.getByRole("button", { name: "repository" });
    expect(projectToggleFinal).toHaveAttribute("aria-expanded", "true");
  });

  it("remembers collapsed '最近' group state in localStorage across remounts", async () => {
    const user = userEvent.setup();
    const recentSession: Session = {
      id: "recent-1",
      workspaceRoot: "/recent",
      projectless: true,
      title: "闲聊任务",
      createdAt: 10,
    };
    const props = {
      sessions: [recentSession],
      projects: [],
      selectedId: undefined,
      disabled: false,
      readCompletedSessions: new Set<string>(),
      runtimePresentation: { tone: "success", label: "Ready" } as const,
      onCreate: vi.fn(),
      onCreateInProject: vi.fn(),
      onSelect: vi.fn(),
      onRename: vi.fn(),
      onDelete: vi.fn(),
      onDeleteProject: vi.fn(),
      onOpenSettings: vi.fn(),
    };

    const { unmount } = render(<SessionSidebar {...props} />);

    const recentToggle = screen.getByRole("button", { name: "最近" });
    expect(recentToggle).toHaveAttribute("aria-expanded", "true");

    // Collapse "最近"
    await user.click(recentToggle);
    expect(recentToggle).toHaveAttribute("aria-expanded", "false");

    // Unmount and remount
    unmount();
    render(<SessionSidebar {...props} />);
    const recentToggleAfterRemount = screen.getByRole("button", { name: "最近" });
    expect(recentToggleAfterRemount).toHaveAttribute("aria-expanded", "false");
  });

  it("auto-reveals a collapsed project when switching to a session inside it, but allows manual collapse while active", async () => {
    const user = userEvent.setup();
    const otherSession: Session = {
      id: "session-other",
      workspaceRoot: "/other",
      projectless: true,
      title: "Other Thread",
      createdAt: 1,
    };
    const props = {
      sessions: [managedSession, otherSession],
      projects: [],
      selectedId: "session-other",
      disabled: false,
      readCompletedSessions: new Set<string>(),
      runtimePresentation: { tone: "success", label: "Ready" } as const,
      onCreate: vi.fn(),
      onCreateInProject: vi.fn(),
      onSelect: vi.fn(),
      onRename: vi.fn(),
      onDelete: vi.fn(),
      onDeleteProject: vi.fn(),
      onOpenSettings: vi.fn(),
    };

    const { rerender } = render(<SessionSidebar {...props} />);

    const projectToggle = screen.getByRole("button", { name: "repository" });
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");

    // Collapse the repository project
    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "false");

    // Switch selectedId to the managedSession inside repository project
    rerender(<SessionSidebar {...props} selectedId={managedSession.id} />);

    // It should auto-reveal (expand) the repository project!
    expect(projectToggle).toHaveAttribute("aria-expanded", "true");

    // Now while active on managedSession, user manually collapses repository project
    await user.click(projectToggle);
    expect(projectToggle).toHaveAttribute("aria-expanded", "false");

    // Re-rendering with the same selectedId should NOT force re-expansion
    rerender(<SessionSidebar {...props} selectedId={managedSession.id} />);
    expect(projectToggle).toHaveAttribute("aria-expanded", "false");
  });
});
