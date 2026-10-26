import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EidosRuntimeAPI, Project, Session } from "../contracts.js";
import type { SidebarTarget } from "../../../shared/sidebar.js";
import { SessionSidebar } from "./SessionSidebar.js";

const project: Project = {
  id: "11111111-1111-1111-1111-111111111111", name: "Eidos", workspaceRoot: "/workspace",
  gitAvailable: false, createdAt: 1, updatedAt: 1,
};
const session: Session = {
  id: "22222222-2222-2222-2222-222222222222", title: "任务 A", project,
  workspaceRoot: "/workspace", taskStatus: "in_progress", activeRunStatus: "waiting_approval",
  createdAt: 2, updatedAt: 2,
};
const projectPin: SidebarTarget = { kind: "project", id: project.id };
const sessionPin: SidebarTarget = { kind: "session", id: session.id };

function props(pins: SidebarTarget[] = []) {
  return {
    pins, sessions: [session], projects: [project], selectedId: session.id, disabled: false,
    readCompletedSessions: new Set<string>(), runtimePresentation: { tone: "success", label: "Ready" } as const,
    onCreate: vi.fn(), onCreateInProject: vi.fn(), onSelect: vi.fn(), onRename: vi.fn(),
    onDelete: vi.fn(), onDeleteProject: vi.fn(), onOpenSettings: vi.fn(), onSetPinned: vi.fn(),
  };
}

beforeEach(() => {
  localStorage.clear();
  window.eidosRuntime = {
    memory: vi.fn(async () => ({ scopes: [], documents: [] })),
  } as unknown as EidosRuntimeAPI;
});
afterEach(() => { vi.useRealTimers(); });

describe("mixed sidebar pinning", () => {
  it("pins a project from its context menu and moves the existing project group", async () => {
    const initial = props();
    const { rerender } = render(<SessionSidebar {...initial} />);
    expect(screen.queryByRole("region", { name: "置顶" })).not.toBeInTheDocument();
    fireEvent.contextMenu(screen.getByRole("button", { name: "Eidos" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "置顶" }));
    expect(initial.onSetPinned).toHaveBeenCalledWith(projectPin, true);
    rerender(<SessionSidebar {...initial} pins={[projectPin]} />);
    const pinned = screen.getByRole("region", { name: "置顶" });
    expect(within(pinned).getByRole("region", { name: "Eidos" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Eidos" })).toHaveLength(1);
    expect(within(pinned).getByRole("button", { name: "Eidos" })).toHaveFocus();
    fireEvent.click(within(pinned).getByRole("button", { name: "在 Eidos 中新建会话" }));
    expect(initial.onCreateInProject).toHaveBeenCalledWith("/workspace");
  });

  it("shows a pinned Session once, preserves status and selection, and supports cancel pinning", async () => {
    const initial = props([sessionPin, projectPin]);
    const { rerender } = render(<SessionSidebar {...initial} />);
    const pinned = screen.getByRole("region", { name: "置顶" });
    expect(pinned.querySelectorAll("[data-session-id]")).toHaveLength(1);
    expect(screen.getAllByText("任务 A")).toHaveLength(1);
    expect(within(pinned).getByLabelText("等待批准")).toBeInTheDocument();
    const button = within(pinned).getByRole("button", { name: /任务 A/ });
    expect(button).toHaveAttribute("aria-current", "page");
    fireEvent.click(button);
    expect(initial.onSelect).toHaveBeenCalledWith(session);
    fireEvent.keyDown(button, { key: "F10", shiftKey: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: "取消置顶" }));
    expect(initial.onSetPinned).toHaveBeenCalledWith(sessionPin, false);
    rerender(<SessionSidebar {...initial} pins={[]} />);
    expect(screen.queryByRole("region", { name: "置顶" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Eidos" })).getByText("任务 A")).toBeInTheDocument();
  });

  it("supports empty projects and projectless Sessions without offering to pin the recent group", async () => {
    const recent: Session = { ...session, id: "33333333-3333-3333-3333-333333333333", project: undefined, projectless: true };
    const { rerender } = render(<SessionSidebar {...props([projectPin])} sessions={[]} />);
    expect(within(screen.getByRole("region", { name: "置顶" })).getByText("暂无会话")).toBeInTheDocument();
    rerender(<SessionSidebar {...props([{ kind: "session", id: recent.id }])} sessions={[recent]} />);
    const pinned = screen.getByRole("region", { name: "置顶" });
    expect(within(pinned).getByText("任务 A")).toBeInTheDocument();
    expect(pinned.querySelector(".workspace-add")).toBeNull();
  });

  it("keeps project deletion disabled when all its Sessions are individually pinned", async () => {
    render(<SessionSidebar {...props([sessionPin])} />);
    const group = screen.getByRole("region", { name: "Eidos" });
    expect(within(group).getByText("会话已置顶")).toBeInTheDocument();
    fireEvent.contextMenu(within(group).getByRole("button", { name: "Eidos" }));
    expect(await screen.findByRole("menuitem", { name: "删除项目" })).toBeDisabled();
  });

  it("does not scroll back when pin placement changes with the same selected Session", async () => {
    vi.useFakeTimers();
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    const initial = props();
    const { rerender, unmount } = render(<SessionSidebar {...initial} />);
    try {
      await act(async () => { await vi.advanceTimersByTimeAsync(500); });
      scroll.mockClear();
      const nav = screen.getByRole("navigation");
      nav.scrollTop = 100;
      rerender(<SessionSidebar {...initial} pins={[sessionPin, projectPin]} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(500); });
      expect(scroll).not.toHaveBeenCalled();
      expect(nav.scrollTop).toBe(100);
      expect(nav.querySelectorAll('button[aria-current="page"]')).toHaveLength(1);
    } finally {
      unmount();
      scroll.mockRestore();
    }
  });

  it("disables only the pin action while its mutation is pending", async () => {
    render(<SessionSidebar {...props()} pendingPinTargets={new Set([`session:${session.id}`])} />);
    fireEvent.contextMenu(screen.getByRole("button", { name: /任务 A/ }));
    expect(await screen.findByRole("menuitem", { name: "置顶" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "编辑标题" })).not.toBeDisabled();
  });

  it("returns keyboard focus to a collapsed project when its pinned Session is unpinned", async () => {
    const initial = props([sessionPin]);
    const { rerender } = render(<SessionSidebar {...initial} />);
    fireEvent.click(screen.getByRole("button", { name: "Eidos" }));
    const button = within(screen.getByRole("region", { name: "置顶" })).getByRole("button", { name: /任务 A/ });
    button.focus();
    fireEvent.keyDown(button, { key: "F10", shiftKey: true });
    fireEvent.click(await screen.findByRole("menuitem", { name: "取消置顶" }));
    rerender(<SessionSidebar {...initial} pins={[]} />);
    const projectButton = screen.getByRole("button", { name: "Eidos" });
    expect(projectButton).toHaveAttribute("aria-expanded", "false");
    expect(projectButton).toHaveFocus();
  });
});
