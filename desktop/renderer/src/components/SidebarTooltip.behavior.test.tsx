import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { Session } from "../contracts.js";
import {
  SidebarTooltip,
  formatSessionTime,
  formatDisplayPath,
  buildProjectTooltipRoots,
  type ProjectTooltipData,
  type SessionTooltipData,
} from "./SidebarTooltip.js";

describe("SidebarTooltip formatSessionTime", () => {
  it("returns '刚刚' for timestamp within the last minute", () => {
    const now = Date.now();
    const result = formatSessionTime(now - 10 * 1000);
    expect(result.friendly).toBe("刚刚");
    expect(result.exact).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  });

  it("returns 'X 分钟前' for timestamp within an hour", () => {
    const now = Date.now();
    const result = formatSessionTime(now - 15 * 60 * 1000);
    expect(result.friendly).toBe("15 分钟前");
  });

  it("returns '今天 HH:mm' for earlier hours on the same day", () => {
    const now = new Date();
    // 3 hours ago, but check if still same day
    const threeHoursAgo = new Date(now.getTime() - 3 * 3600 * 1000);
    if (threeHoursAgo.getDate() === now.getDate()) {
      const result = formatSessionTime(threeHoursAgo.getTime());
      expect(result.friendly).toMatch(/^今天 \d{2}:\d{2}$/);
    }
  });

  it("handles 0 or invalid timestamp gracefully", () => {
    expect(formatSessionTime(0).friendly).toBe("刚刚");
    expect(formatSessionTime(NaN).friendly).toBe("刚刚");
  });
});

describe("SidebarTooltip rendering", () => {
  const dummyRect = {
    top: 100,
    bottom: 132,
    left: 20,
    right: 220,
    width: 200,
    height: 32,
    x: 20,
    y: 100,
    toJSON: () => {},
  } as DOMRect;

  it("formats home paths with tilde abbreviation", () => {
    expect(formatDisplayPath("/Users/xielei/Documents/test1/Office-Test")).toBe("~/Documents/test1/Office-Test");
    expect(formatDisplayPath("/home/ubuntu/repo")).toBe("~/repo");
    expect(formatDisplayPath("/var/log/app")).toBe("/var/log/app");
    expect(formatDisplayPath("")).toBe("");
  });

  it("renders Project hover tooltip with name and single unified path row", () => {
    const projectState: ProjectTooltipData = {
      kind: "project",
      projectName: "Eidos Runtime",
      workspaceRoot: "/Users/developer/Projects/Eidos",
      sessionCount: 3,
      isPinned: true,
      projectless: false,
      rect: dummyRect,
    };

    render(<SidebarTooltip state={projectState} />);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toBeInTheDocument();
    expect(screen.getByText("Eidos Runtime")).toBeInTheDocument();
    expect(screen.getByText("3 次对话")).toBeInTheDocument();
    expect(screen.getByLabelText("已置顶")).toBeInTheDocument();
    expect(screen.getByText("~/Projects/Eidos")).toBeInTheDocument();
  });

  it("renders Project hover tooltip with multiple roots, session count and pin", () => {
    const projectState: ProjectTooltipData = {
      kind: "project",
      projectName: "Office-Test",
      workspaceRoot: "/Users/xielei/Documents/test1/Office-Test",
      sessionCount: 10,
      isPinned: true,
      roots: [
        {
          id: "r1",
          path: "/Users/xielei/Documents/test1/Office-Test",
          name: "Office-Test",
          isPrimary: true,
        },
        {
          id: "r2",
          path: "/Users/xielei/Documents/test1/office-backend",
          name: "office-backend",
          isPrimary: false,
        },
        {
          id: "r3",
          path: "/Users/xielei/Documents/common/shared-contracts",
          name: "shared-contracts",
          isPrimary: false,
          shared: true,
        },
      ],
      projectless: false,
      rect: dummyRect,
    };

    render(<SidebarTooltip state={projectState} />);

    expect(screen.getByText("Office-Test")).toBeInTheDocument();
    expect(screen.getByText("10 次对话")).toBeInTheDocument();
    expect(screen.getByLabelText("已置顶")).toBeInTheDocument();

    expect(screen.getByText("~/Documents/test1/Office-Test")).toBeInTheDocument();
    expect(screen.getByText("~/Documents/test1/office-backend")).toBeInTheDocument();
    expect(screen.getByText("~/Documents/common/shared-contracts")).toBeInTheDocument();

    const rootRows = screen.getAllByRole("listitem");
    expect(rootRows).toHaveLength(3);
    // First root is primary: has primary icon modifier
    expect(rootRows[0]?.querySelector(".sidebar-hover-card__root-icon--primary")).toBeTruthy();
    // Second and third are non-primary / shared: have muted icon modifier
    expect(rootRows[1]?.querySelector(".sidebar-hover-card__root-icon--muted")).toBeTruthy();
    expect(rootRows[2]?.querySelector(".sidebar-hover-card__root-icon--muted")).toBeTruthy();
  });

  it("buildProjectTooltipRoots sorts primary root to the first position", () => {
    const result = buildProjectTooltipRoots({
      workspaceRoot: "/work/fallback",
      project: {
        primaryRootId: "secondary-id",
        roots: [
          { id: "first-id", path: "/work/first" },
          { id: "secondary-id", path: "/work/second", shared: true },
        ],
      },
    });

    expect(result).toHaveLength(2);
    expect(result?.[0]?.id).toBe("secondary-id");
    expect(result?.[0]?.isPrimary).toBe(true);
    expect(result?.[0]?.shared).toBe(true);
    expect(result?.[1]?.id).toBe("first-id");
    expect(result?.[1]?.isPrimary).toBe(false);
  });

  it("buildProjectTooltipRoots falls back to single root when roots array is missing", () => {
    const result = buildProjectTooltipRoots({
      workspaceRoot: "/work/main-project",
      project: {
        roots: [],
      },
    });

    expect(result).toEqual([
      {
        id: "primary",
        path: "/work/main-project",
        name: "main-project",
        isPrimary: true,
      },
    ]);
  });

  it("renders Project hover tooltip for projectless recent group", () => {
    const recentState: ProjectTooltipData = {
      kind: "project",
      projectName: "最近",
      workspaceRoot: "",
      projectless: true,
      rect: dummyRect,
    };

    render(<SidebarTooltip state={recentState} />);

    expect(screen.getByText("最近")).toBeInTheDocument();
    expect(screen.getByText("最近会话")).toBeInTheDocument();
    expect(screen.getByText("工作区说明")).toBeInTheDocument();
    expect(screen.getByText(/临时工作区/)).toBeInTheDocument();
  });

  it("renders Session hover tooltip with full title, parent project, and time", () => {
    const session: Session = {
      id: "session-1",
      workspaceRoot: "/workspace/eidos",
      title: "完整长任务标题：优化桌面侧边栏与交互效果",
      taskStatus: "in_progress",
      createdAt: Date.now() - 5 * 60 * 1000,
      updatedAt: Date.now() - 5 * 60 * 1000,
      worktree: {
        worktreeId: "wt-1",
        projectId: "proj-1",
        repositoryRoot: "/workspace/eidos",
        worktreeRoot: "/workspace/eidos/wt-1",
        baseRef: "main",
        baseCommit: "abc",
        branch: "feat/hover-tooltips",
        state: "active",
      },
    };

    const sessionState: SessionTooltipData = {
      kind: "session",
      session,
      projectDisplayName: "Eidos",
      statusLabel: "进行中",
      statusTone: "progress",
      rect: dummyRect,
    };

    render(<SidebarTooltip state={sessionState} />);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toBeInTheDocument();
    expect(screen.getByText("完整长任务标题：优化桌面侧边栏与交互效果")).toBeInTheDocument();
    expect(screen.getByText("所属项目")).toBeInTheDocument();
    expect(screen.getByText("Eidos")).toBeInTheDocument();
    expect(screen.getByText("最近会话")).toBeInTheDocument();
    expect(screen.getByText(/^\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}\)$/)).toBeInTheDocument();
    expect(screen.getByText("5 分钟前")).toBeInTheDocument();
    expect(screen.getByText("关联分支")).toBeInTheDocument();
    expect(screen.getByText("feat/hover-tooltips")).toBeInTheDocument();
    expect(screen.getByText("任务状态")).toBeInTheDocument();
    expect(screen.getByText("进行中")).toBeInTheDocument();
  });

  it("does not render 所属项目 for projectless (最近) session", () => {
    const session: Session = {
      id: "session-recent",
      workspaceRoot: "/workspace/temp",
      title: "临时快速任务",
      taskStatus: "completed",
      projectless: true,
      createdAt: Date.now() - 10 * 60 * 1000,
      updatedAt: Date.now() - 10 * 60 * 1000,
    };

    const sessionState: SessionTooltipData = {
      kind: "session",
      session,
      projectDisplayName: "最近",
      projectless: true,
      rect: dummyRect,
    };

    render(<SidebarTooltip state={sessionState} />);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toBeInTheDocument();
    expect(screen.getByText("临时快速任务")).toBeInTheDocument();
    expect(screen.getByText("最近会话")).toBeInTheDocument();
    expect(screen.queryByText("所属项目")).not.toBeInTheDocument();
    expect(screen.queryByText("来源会话")).not.toBeInTheDocument();
  });

  it("opens the source Session by clicking its current name", () => {
    const source: Session = {
      id: "source-session", title: "原会话的新名称", workspaceRoot: "/workspace/source",
      taskStatus: "completed", createdAt: 1, updatedAt: 2,
    };
    const session: Session = {
      ...source, id: "fork-session", title: "分叉会话",
      forkedFrom: { sessionId: source.id, runId: "run", itemId: "answer", itemCount: 2, createdAt: 3 },
    };
    const onOpenSourceSession = vi.fn();
    render(<SidebarTooltip state={{ kind: "session", session, rect: dummyRect }}
      sourceSession={source} onOpenSourceSession={onOpenSourceSession} />);

    expect(screen.getByText("来源会话")).toBeInTheDocument();
    const link = screen.getByRole("button", { name: "打开来源会话：原会话的新名称" });
    fireEvent.click(link);
    expect(onOpenSourceSession).toHaveBeenCalledWith(source);
  });

  it("opens the scheduled task from its source label without showing internal metadata", () => {
    const origin = { automationId: "task-id", revision: 1, occurrenceId: "occurrence-id", scheduledFor: "manual:opaque-id" };
    const session: Session = { id: "scheduled-session", title: "每日一个为什么", workspaceRoot: "/workspace/scheduled",
      taskStatus: "completed", createdAt: 1, updatedAt: 2, automationOrigin: origin };
    const open = vi.fn();
    const { rerender } = render(<SidebarTooltip state={{ kind: "session", session, rect: dummyRect }} onOpenAutomation={open} />);
    expect(screen.getByText("会话来源")).toBeInTheDocument();
    const link = screen.getByRole("button", { name: "打开来源定时任务" });
    expect(link).toHaveTextContent("定时任务");
    expect(screen.getByRole("tooltip")).not.toHaveTextContent("manual:");
    expect(screen.getByRole("tooltip")).not.toHaveTextContent("task-id");
    fireEvent.click(link);
    expect(open).toHaveBeenCalledExactlyOnceWith(origin.automationId);
    rerender(<SidebarTooltip state={{ kind: "session", session, rect: dummyRect }} onOpenAutomation={open} disabled />);
    expect(screen.getByRole("button", { name: "打开来源定时任务" })).toBeDisabled();
  });

  it("shows an unavailable source without a broken navigation button", () => {
    const session: Session = {
      id: "fork-session", title: "分叉会话", workspaceRoot: "/workspace/fork",
      taskStatus: "completed", createdAt: 1, updatedAt: 2,
      forkedFrom: { sessionId: "missing-source", runId: "run", itemId: "answer", itemCount: 2, createdAt: 3 },
    };
    render(<SidebarTooltip state={{ kind: "session", session, rect: dummyRect }} />);
    expect(screen.getByText("来源会话不可用")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /打开来源会话/ })).not.toBeInTheDocument();
  });

  it("fires onMouseEnter and onMouseLeave on the tooltip card", () => {
    const onMouseEnter = vi.fn();
    const onMouseLeave = vi.fn();
    const projectState: ProjectTooltipData = {
      kind: "project",
      projectName: "Eidos",
      workspaceRoot: "/workspace/eidos",
      projectless: false,
      rect: dummyRect,
    };

    render(
      <SidebarTooltip
        state={projectState}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      />,
    );

    const tooltip = screen.getByRole("tooltip");
    fireEvent.mouseEnter(tooltip);
    expect(onMouseEnter).toHaveBeenCalledTimes(1);

    fireEvent.mouseLeave(tooltip);
    expect(onMouseLeave).toHaveBeenCalledTimes(1);
  });

  it("returns null when state is null", () => {
    const { container } = render(<SidebarTooltip state={null} />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
