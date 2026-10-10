import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import type { Session } from "../contracts.js";
import {
  SidebarTooltip,
  formatSessionTime,
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

  it("renders Project hover tooltip with name and folder path", () => {
    const projectState: ProjectTooltipData = {
      kind: "project",
      projectName: "Eidos Runtime",
      workspaceRoot: "/Users/developer/Projects/Eidos",
      projectless: false,
      rect: dummyRect,
    };

    render(<SidebarTooltip state={projectState} />);

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toBeInTheDocument();
    expect(screen.getByText("Eidos Runtime")).toBeInTheDocument();
    expect(screen.getByText("文件夹路径")).toBeInTheDocument();
    expect(screen.getByText("/Users/developer/Projects/Eidos")).toBeInTheDocument();
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
