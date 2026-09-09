import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NavigationControls } from "./NavigationControls.js";

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
});
