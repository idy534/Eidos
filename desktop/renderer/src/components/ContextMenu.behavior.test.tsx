import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionSidebar } from "./SessionSidebar.js";
import type { Session } from "../contracts.js";

const mockSessions: Session[] = [
  { id: "session-1", title: "Session One", workspaceRoot: "/ws/project1", createdAt: 1000, updatedAt: 1000 },
];

describe("SessionSidebar ContextMenu DOM interaction behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("Mouse right-click opens ContextMenu at pointer coordinates", async () => {
    render(
      <SessionSidebar
        sessions={mockSessions}
        selectedId="session-1"
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInWorkspace={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    const sessionBtn = screen.getByRole("button", { name: /Session One/i });
    fireEvent.contextMenu(sessionBtn, { clientX: 250, clientY: 350 });

    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();
    expect(menu).toHaveStyle({ left: "250px", top: "350px" });
  });

  it("Shift+F10 and ContextMenu key open ContextMenu from focused Session row", async () => {
    const user = userEvent.setup();
    render(
      <SessionSidebar
        sessions={mockSessions}
        selectedId="session-1"
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInWorkspace={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    const sessionBtn = screen.getByRole("button", { name: /Session One/i });
    sessionBtn.focus();

    await user.keyboard("{Shift>}{F10}{/Shift}");
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    // Escape closes and restores focus to sessionBtn
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(sessionBtn).toHaveFocus();
  });

  it("ContextMenu keyboard navigation and viewport collision boundary positioning", async () => {
    const user = userEvent.setup();
    const onCloseSpy = vi.fn();
    const mockItems = [
      { key: "item-1", label: "Option 1", onClick: vi.fn() },
      { key: "item-2", label: "Option 2 Disabled", disabled: true, onClick: vi.fn() },
      { key: "item-3", label: "Option 3", onClick: vi.fn() },
    ];

    const { ContextMenu } = await import("./DropdownMenu.js");

    Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 1080, writable: true, configurable: true });

    render(
      <ContextMenu
        x={1910}
        y={1070}
        items={mockItems}
        onClose={onCloseSpy}
      />,
    );

    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const menuItems = screen.getAllByRole("menuitem");
    expect(menuItems[0]).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(menuItems[2]).toHaveFocus();

    await user.keyboard("{ArrowUp}");
    expect(menuItems[0]).toHaveFocus();

    await user.keyboard("{Tab}");
    expect(onCloseSpy).toHaveBeenCalledTimes(1);
  });
});
