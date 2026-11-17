import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NavigationControls } from "./NavigationControls.js";
import {
  applyPlatformDataset,
  currentOsUserLabel,
  formatPlatformShortcut,
  isPathWithinRoot,
  joinWorkspacePath,
  pathBasename,
} from "../platform.js";

describe("NavigationControls", () => {
  it("renders buttons with accessible labels and titles", () => {
    render(
      <NavigationControls
        sidebarOpen={true}
        canGoBack={true}
        canGoForward={false}
        onToggleSidebar={vi.fn()}
        onGoBack={vi.fn()}
        onGoForward={vi.fn()}
      />,
    );

    const toggleBtn = screen.getByRole("button", { name: "收起侧边栏" });
    const backBtn = screen.getByRole("button", { name: "后退" });
    const forwardBtn = screen.getByRole("button", { name: "前进" });

    expect(toggleBtn).toBeDefined();
    expect(toggleBtn.getAttribute("title")).toContain("⌘B");
    expect(backBtn).not.toBeDisabled();
    expect(forwardBtn).toBeDisabled();
  });

  it("handles sidebar toggle click", () => {
    const onToggle = vi.fn();
    render(
      <NavigationControls
        sidebarOpen={false}
        canGoBack={false}
        canGoForward={false}
        onToggleSidebar={onToggle}
        onGoBack={vi.fn()}
        onGoForward={vi.fn()}
      />,
    );

    const toggleBtn = screen.getByRole("button", { name: "展开侧边栏" });
    fireEvent.click(toggleBtn);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("handles back and forward clicks when enabled", () => {
    const onBack = vi.fn();
    const onForward = vi.fn();
    render(
      <NavigationControls
        sidebarOpen={true}
        canGoBack={true}
        canGoForward={true}
        onToggleSidebar={vi.fn()}
        onGoBack={onBack}
        onGoForward={onForward}
      />,
    );

    const backBtn = screen.getByRole("button", { name: "后退" });
    const forwardBtn = screen.getByRole("button", { name: "前进" });

    fireEvent.click(backBtn);
    expect(onBack).toHaveBeenCalledTimes(1);

    fireEvent.click(forwardBtn);
    expect(onForward).toHaveBeenCalledTimes(1);
  });

  it("renders traffic light spacer when showTrafficSpacer is true", () => {
    const { container, rerender } = render(
      <NavigationControls
        sidebarOpen={true}
        canGoBack={false}
        canGoForward={false}
        onToggleSidebar={vi.fn()}
        onGoBack={vi.fn()}
        onGoForward={vi.fn()}
        showTrafficSpacer={true}
      />,
    );

    expect(container.querySelector(".window-traffic-spacer")).not.toBeNull();

    rerender(
      <NavigationControls
        sidebarOpen={true}
        canGoBack={false}
        canGoForward={false}
        onToggleSidebar={vi.fn()}
        onGoBack={vi.fn()}
        onGoForward={vi.fn()}
        showTrafficSpacer={false}
      />,
    );

    expect(container.querySelector(".window-traffic-spacer")).toBeNull();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete document.documentElement.dataset.platform;
  });

  it("adapts shortcuts and platform helpers for Windows without changing macOS defaults", () => {
    expect(formatPlatformShortcut("⌘B", false)).toBe("⌘B");
    expect(formatPlatformShortcut("⌘[", false)).toBe("⌘[");
    expect(formatPlatformShortcut("⌘]", false)).toBe("⌘]");
    expect(formatPlatformShortcut("Cmd+Enter", false)).toBe("Cmd+Enter");
    expect(currentOsUserLabel(false)).toBe("当前 macOS 用户");

    expect(formatPlatformShortcut("⌘B", true)).toBe("Ctrl+B");
    expect(formatPlatformShortcut("⌘[", true)).toBe("Alt+←");
    expect(formatPlatformShortcut("⌘]", true)).toBe("Alt+→");
    expect(formatPlatformShortcut("Cmd+Enter", true)).toBe("Ctrl+Enter");
    expect(formatPlatformShortcut("Cmd -", true)).toBe("Ctrl+-");
    expect(currentOsUserLabel(true)).toBe("当前 Windows 用户");

    expect(pathBasename("D:\\Agent Framework\\Eidos")).toBe("Eidos");
    expect(pathBasename("/Users/dev/Eidos/")).toBe("Eidos");
    expect(isPathWithinRoot("d:\\Workspace\\src\\index.ts", "D:\\Workspace")).toBe(true);
    expect(isPathWithinRoot("D:\\Workspace2\\src\\index.ts", "D:\\Workspace")).toBe(false);
    expect(joinWorkspacePath("D:\\Workspace\\", "src/app.ts")).toBe("D:\\Workspace\\src\\app.ts");
    expect(joinWorkspacePath("/workspace/", "src/app.ts")).toBe("/workspace/src/app.ts");

    applyPlatformDataset(document.documentElement, true);
    expect(document.documentElement.dataset.platform).toBe("windows");
    applyPlatformDataset(document.documentElement, false);
    expect(document.documentElement.dataset.platform).toBeUndefined();

    vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Electron/35.0.0",
    );
    render(
      <NavigationControls
        sidebarOpen={true}
        canGoBack={true}
        canGoForward={true}
        onToggleSidebar={vi.fn()}
        onGoBack={vi.fn()}
        onGoForward={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "收起侧边栏" }).getAttribute("title")).toContain("Ctrl+B");
    expect(screen.getByRole("button", { name: "后退" }).getAttribute("title")).toContain("Alt+←");
    expect(screen.getByRole("button", { name: "前进" }).getAttribute("title")).toContain("Alt+→");
  });
});
