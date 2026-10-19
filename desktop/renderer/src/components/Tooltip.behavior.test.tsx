import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Tooltip } from "./Tooltip.js";

describe("Tooltip component", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders trigger child and attaches accessibility attributes", () => {
    render(
      <Tooltip content="复制内容">
        <button type="button" aria-label="复制内容">复制</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "复制内容" });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute("data-tooltip", "复制内容");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("shows tooltip after default delay on mouseenter and hides on mouseleave", async () => {
    render(
      <Tooltip content="从这里分叉" delay={180}>
        <button type="button" aria-label="从这里分叉">分叉</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "从这里分叉" });

    fireEvent.mouseEnter(button);
    // Not visible immediately
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    // Advance timer past delay
    act(() => {
      vi.advanceTimersByTime(200);
    });

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toBeInTheDocument();
    expect(tooltip).toHaveTextContent("从这里分叉");

    // Mouse leave hides tooltip
    fireEvent.mouseLeave(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("supports warm tooltip group switching without delay", async () => {
    render(
      <div>
        <Tooltip content="点赞" delay={180}>
          <button type="button" aria-label="点赞">赞</button>
        </Tooltip>
        <Tooltip content="差评" delay={180}>
          <button type="button" aria-label="差评">踩</button>
        </Tooltip>
      </div>,
    );

    const button1 = screen.getByRole("button", { name: "点赞" });
    const button2 = screen.getByRole("button", { name: "差评" });

    // Hover button 1 and wait for it to show
    fireEvent.mouseEnter(button1);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByRole("tooltip")).toHaveTextContent("点赞");

    // Leave button 1 and instantly enter button 2
    fireEvent.mouseLeave(button1);
    fireEvent.mouseEnter(button2);

    // Warm threshold triggers immediately (0 delay)
    expect(screen.getByRole("tooltip")).toHaveTextContent("差评");
  });

  it("shows tooltip on keyboard focus and dismisses on Escape key", async () => {
    render(
      <Tooltip content="重新回答">
        <button type="button" aria-label="重新回答">重答</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "重新回答" });

    fireEvent.focus(button);
    act(() => {
      vi.advanceTimersByTime(100);
    });

    expect(screen.getByRole("tooltip")).toHaveTextContent("重新回答");

    fireEvent.keyDown(button, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("hides tooltip on click", () => {
    const handleClick = vi.fn();
    render(
      <Tooltip content="复制">
        <button type="button" aria-label="复制" onClick={handleClick}>复制</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "复制" });
    fireEvent.mouseEnter(button);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    fireEvent.click(button);
    expect(handleClick).toHaveBeenCalled();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("renders keyboard shortcut badge when provided", () => {
    render(
      <Tooltip content="新建会话" shortcut="⌘N">
        <button type="button" aria-label="新建会话">新建</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "新建会话" });
    fireEvent.mouseEnter(button);
    act(() => {
      vi.advanceTimersByTime(200);
    });

    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent("新建会话");
    expect(tooltip.querySelector(".eidos-tooltip__shortcut")).toHaveTextContent("⌘N");
  });

  it("does not show tooltip when disabled is true", () => {
    render(
      <Tooltip content="禁用操作" disabled>
        <button type="button" aria-label="禁用操作">禁用</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "禁用操作" });
    fireEvent.mouseEnter(button);
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("clears native title on hover to prevent browser system tooltip double-popup", () => {
    render(
      <Tooltip content="查看详情">
        <button type="button" title="查看详情" aria-label="查看详情">按钮</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "查看详情" });
    expect(button.getAttribute("title")).toBe("查看详情");

    fireEvent.mouseEnter(button);
    expect(button.getAttribute("title")).toBeNull();
  });

  it("does not show tooltip on focus when clicked or pointer pressed", () => {
    render(
      <Tooltip content="重新回答">
        <button type="button" aria-label="重新回答">重答</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "重新回答" });

    // Click the button (which suppresses tooltip on subsequent focus)
    fireEvent.click(button);

    // Focus immediately after click (or when dialog restores focus)
    fireEvent.focus(button);
    act(() => {
      vi.advanceTimersByTime(200);
    });

    // Tooltip must NOT appear!
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("does not show tooltip when focus is restored with data-restored-focus", () => {
    render(
      <Tooltip content="重新回答">
        <button type="button" aria-label="重新回答">重答</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "重新回答" });
    button.setAttribute("data-restored-focus", "true");

    fireEvent.focus(button);
    act(() => {
      vi.advanceTimersByTime(200);
    });

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    // Attribute should be cleaned up
    expect(button.hasAttribute("data-restored-focus")).toBe(false);
  });

  it("dismisses visible tooltip immediately when clicking outside (blank space)", () => {
    render(
      <div>
        <Tooltip content="重新回答">
          <button type="button" aria-label="重新回答">重答</button>
        </Tooltip>
        <div data-testid="blank-space">空白处</div>
      </div>,
    );

    const button = screen.getByRole("button", { name: "重新回答" });
    fireEvent.mouseEnter(button);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    // Click outside on blank space
    const blank = screen.getByTestId("blank-space");
    fireEvent.pointerDown(blank);

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("dismisses visible tooltip immediately when window scrolls", () => {
    render(
      <Tooltip content="重新回答">
        <button type="button" aria-label="重新回答">重答</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "重新回答" });
    fireEvent.mouseEnter(button);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    // User scrolls window
    fireEvent.scroll(window);

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("aborts showing tooltip if mouse leaves trigger before delay finishes", () => {
    render(
      <Tooltip content="复制" delay={200}>
        <button type="button" aria-label="复制">复制</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "复制" });
    fireEvent.mouseEnter(button);

    // Mouse leaves at 50ms before delay finishes
    act(() => {
      vi.advanceTimersByTime(50);
    });
    fireEvent.mouseLeave(button);

    // Advance past original delay
    act(() => {
      vi.advanceTimersByTime(200);
    });

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("dismisses visible tooltip when disabled prop becomes true", () => {
    const { rerender } = render(
      <Tooltip content="操作" disabled={false}>
        <button type="button" aria-label="操作">按钮</button>
      </Tooltip>,
    );

    const button = screen.getByRole("button", { name: "操作" });
    fireEvent.mouseEnter(button);
    act(() => {
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    // Prop update: disabled becomes true (e.g. while submitting)
    rerender(
      <Tooltip content="操作" disabled={true}>
        <button type="button" aria-label="操作">按钮</button>
      </Tooltip>,
    );

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
