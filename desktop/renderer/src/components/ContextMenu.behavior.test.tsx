import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionSidebar } from "./SessionSidebar.js";
import { ContextMenu } from "./DropdownMenu.js";
import type { EidosRuntimeAPI, Session } from "../contracts.js";

const mockSessions: Session[] = [
  { id: "session-1", title: "Session One", workspaceRoot: "/ws/project1", createdAt: 1000, updatedAt: 1000 },
];
const innerWidthDescriptor = Object.getOwnPropertyDescriptor(window, "innerWidth");
const innerHeightDescriptor = Object.getOwnPropertyDescriptor(window, "innerHeight");
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");

describe("SessionSidebar ContextMenu DOM interaction behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.eidosRuntime = {
      memory: vi.fn(async () => ({ scopes: [], documents: [] })),
    } as unknown as EidosRuntimeAPI;
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      writable: true,
      configurable: true,
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    if (innerWidthDescriptor) Object.defineProperty(window, "innerWidth", innerWidthDescriptor);
    if (innerHeightDescriptor) Object.defineProperty(window, "innerHeight", innerHeightDescriptor);
    if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
    else delete (navigator as Partial<Navigator>).clipboard;
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
        onCreateInProject={vi.fn()}
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
        onCreateInProject={vi.fn()}
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
        onCreateInProject={vi.fn()}
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

  it("opens copy submenu on hover and copies session name to clipboard on click", async () => {
    render(
      <SessionSidebar
        sessions={mockSessions}
        selectedId="session-1"
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    const sessionBtn = screen.getByRole("button", { name: /Session One/i });
    fireEvent.contextMenu(sessionBtn, { clientX: 200, clientY: 200 });

    const copyBtn = screen.getByRole("menuitem", { name: "复制" });
    expect(copyBtn).toBeInTheDocument();
    expect(copyBtn).toHaveAttribute("aria-haspopup", "menu");
    expect(copyBtn).toHaveAttribute("aria-expanded", "false");

    // Hover on 复制 opens submenu
    fireEvent.pointerEnter(copyBtn);
    expect(copyBtn).toHaveAttribute("aria-expanded", "true");
    expect(copyBtn).toHaveClass("is-submenu-open");

    const copyNameBtn = screen.getByRole("menuitem", { name: "复制名称" });
    const copyWorkspaceBtn = screen.getByRole("menuitem", { name: "复制项目目录" });
    expect(copyNameBtn).toBeInTheDocument();
    expect(copyWorkspaceBtn).toBeInTheDocument();

    // Click 复制名称
    fireEvent.click(copyNameBtn);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("Session One");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("copies project workspaceRoot to clipboard on clicking copy workspace", async () => {
    render(
      <SessionSidebar
        sessions={mockSessions}
        selectedId="session-1"
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    const sessionBtn = screen.getByRole("button", { name: /Session One/i });
    fireEvent.contextMenu(sessionBtn, { clientX: 200, clientY: 200 });

    const copyBtn = screen.getByRole("menuitem", { name: "复制" });
    fireEvent.pointerEnter(copyBtn);

    const copyWorkspaceBtn = screen.getByRole("menuitem", { name: "复制项目目录" });
    fireEvent.click(copyWorkspaceBtn);

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("/ws/project1");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("keyboard navigation into and out of copy submenu", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    render(
      <SessionSidebar
        sessions={mockSessions}
        selectedId="session-1"
        disabled={false}
        readCompletedSessions={new Set()}
        runtimePresentation={{ tone: "success", label: "Ready" }}
        onSetPinned={vi.fn()}
        onCreate={vi.fn()}
        onCreateInProject={vi.fn()}
        onSelect={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );

    const sessionBtn = screen.getByRole("button", { name: /Session One/i });
    sessionBtn.focus();

    await user.keyboard("{ContextMenu}");
    const copyBtn = screen.getByRole("menuitem", { name: "复制" });
    expect(copyBtn).toBeInTheDocument();

    // ArrowDown to 复制
    await user.keyboard("{ArrowDown}");
    expect(copyBtn).toHaveFocus();

    // ArrowRight opens submenu and focuses first item
    await user.keyboard("{ArrowRight}");
    const copyNameBtn = screen.getByRole("menuitem", { name: "复制名称" });
    expect(copyNameBtn).toBeInTheDocument();
    expect(copyNameBtn).toHaveFocus();

    // ArrowLeft closes submenu and restores focus to parent item
    await user.keyboard("{ArrowLeft}");
    expect(screen.queryByRole("menuitem", { name: "复制名称" })).not.toBeInTheDocument();
    expect(copyBtn).toHaveFocus();

    // ArrowRight again, then ArrowDown to 复制项目目录
    await user.keyboard("{ArrowRight}");
    const copyWorkspaceBtn = screen.getByRole("menuitem", { name: "复制项目目录" });
    await user.keyboard("{ArrowDown}");
    expect(copyWorkspaceBtn).toHaveFocus();

    // Enter activates 复制项目目录
    await user.keyboard("{Enter}");
    expect(writeText).toHaveBeenCalledWith("/ws/project1");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(sessionBtn).toHaveFocus();
  });
});
