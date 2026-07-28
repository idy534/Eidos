import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionSidebar } from "./SessionSidebar.js";
import { ContextMenu } from "./DropdownMenu.js";
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

  it("Shift+F10 opens ContextMenu from focused Session row and Escape restores focus", async () => {
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

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(sessionBtn).toHaveFocus();
  });

  it("ContextMenu key opens ContextMenu from focused Session row and Escape restores focus", async () => {
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

    await user.keyboard("{ContextMenu}");
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(sessionBtn).toHaveFocus();
  });

  it("ContextMenu viewport collision boundary positioning with explicit mock element rects", () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("task-context-menu")) {
        return {
          width: 240,
          height: 180,
          top: 0,
          left: 0,
          right: 240,
          bottom: 180,
          x: 0,
          y: 0,
          toJSON: () => {},
        };
      }
      return { width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => {} };
    });

    Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 1080, writable: true, configurable: true });

    const onClose = vi.fn();
    const mockItems = [{ key: "1", label: "Item 1", onClick: vi.fn() }];

    // 1. Right edge collision (x = 1800 -> left = 1920 - 240 - 8 = 1672px)
    const { unmount: u1 } = render(
      <ContextMenu x={1800} y={100} items={mockItems} onClose={onClose} />,
    );
    expect(screen.getByRole("menu")).toHaveStyle({ left: "1672px", top: "100px" });
    u1();

    // 2. Bottom edge collision (y = 1000 -> top = 1080 - 180 - 8 = 892px)
    const { unmount: u2 } = render(
      <ContextMenu x={100} y={1000} items={mockItems} onClose={onClose} />,
    );
    expect(screen.getByRole("menu")).toHaveStyle({ left: "100px", top: "892px" });
    u2();

    // 3. Both edges collision (x = 1900, y = 1050 -> left = 1672px, top = 892px)
    const { unmount: u3 } = render(
      <ContextMenu x={1900} y={1050} items={mockItems} onClose={onClose} />,
    );
    expect(screen.getByRole("menu")).toHaveStyle({ left: "1672px", top: "892px" });
    u3();

    // 4. Minimum boundary margin (small window: innerWidth = 100, innerHeight = 100 -> left = 8px, top = 8px)
    Object.defineProperty(window, "innerWidth", { value: 100, writable: true, configurable: true });
    Object.defineProperty(window, "innerHeight", { value: 100, writable: true, configurable: true });

    const { unmount: u4 } = render(
      <ContextMenu x={1000} y={1000} items={mockItems} onClose={onClose} />,
    );
    expect(screen.getByRole("menu")).toHaveStyle({ left: "8px", top: "8px" });
    u4();
  });
});
