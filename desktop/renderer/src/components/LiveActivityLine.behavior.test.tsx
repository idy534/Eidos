import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveActivityLine } from "./LiveActivityLine.js";

const thinking = { summaryId: "rs:0", title: "确认技能目录", receivedAt: 1, body: "先列出目录，再按名称匹配。", previous: ["上一步"] };

afterEach(() => vi.useRealTimers());

describe("LiveActivityLine", () => {
  it("is a plain status without summary text", () => {
    render(<LiveActivityLine text="正在思考" />);
    expect(screen.getByRole("status")).toHaveTextContent("正在思考");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("opens a summary popover from the row and closes it with Escape, returning focus", () => {
    render(<LiveActivityLine text={thinking.title} thinking={thinking} />);
    const row = screen.getByRole("button", { name: /确认技能目录/ });
    expect(row).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    const dialog = screen.getByRole("dialog", { name: "思考过程" });
    expect(dialog).toHaveTextContent("先列出目录，再按名称匹配。");
    expect(dialog).toHaveTextContent("上一步");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(row).toHaveFocus();
  });

  it("closes on an outside press but not on a press inside the popover", () => {
    render(<div><span>outside</span><LiveActivityLine text={thinking.title} thinking={thinking} /></div>);
    fireEvent.click(screen.getByRole("button"));
    fireEvent.pointerDown(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByText("outside"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps an already-opened popover visible when the summary body goes away until dismissed", () => {
    render(<div><span>outside</span><LiveActivityLine text={thinking.title} thinking={thinking} /></div>);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("dialog", { name: "思考过程" })).toHaveTextContent("先列出目录，再按名称匹配。");

    // Current thinking round ends and row returns to "正在思考", but the open popover stays visible.
    fireEvent.pointerDown(screen.getByRole("dialog"));
    expect(screen.getByRole("dialog", { name: "思考过程" })).toBeInTheDocument();
  });

  it("retains the last thinking body while open across thinking transitions and downgrades after outside dismissal", () => {
    const { rerender } = render(<div><span>outside</span><LiveActivityLine text={thinking.title} thinking={thinking} /></div>);
    fireEvent.click(screen.getByRole("button"));
    rerender(<div><span>outside</span><LiveActivityLine text="正在思考" /></div>);
    const dialog = screen.getByRole("dialog", { name: "思考过程" });
    expect(dialog).toHaveTextContent("先列出目录，再按名称匹配。");
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");

    // A new thinking round with body updates the open popover in place.
    const nextThinking = { summaryId: "rs:1", title: "读取配置", receivedAt: 2, body: "检查配置文件内容。", previous: ["确认技能目录"] };
    rerender(<div><span>outside</span><LiveActivityLine text={nextThinking.title} thinking={nextThinking} /></div>);
    expect(screen.getByRole("dialog", { name: "思考过程" })).toHaveTextContent("检查配置文件内容。");

    // Returning to "正在思考" again keeps the latest thinking until the user clicks outside.
    rerender(<div><span>outside</span><LiveActivityLine text="正在思考" /></div>);
    expect(screen.getByRole("dialog", { name: "思考过程" })).toHaveTextContent("检查配置文件内容。");
    fireEvent.pointerDown(screen.getByText("outside"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("flips the popover below the row when near the top of the feed and keeps it above when space allows", () => {
    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("feed")) {
        return { top: 48, bottom: 648, left: 0, right: 800, width: 800, height: 600, x: 0, y: 48, toJSON: () => ({}) };
      }
      if (this.classList.contains("live-activity")) {
        const nearTop = this.getAttribute("data-test-pos") !== "bottom";
        const top = nearTop ? 90 : 520;
        return { top, bottom: top + 24, left: 24, right: 324, width: 300, height: 24, x: 24, y: top, toJSON: () => ({}) };
      }
      if (this.classList.contains("live-activity-popover")) {
        return { top: 0, bottom: 180, left: 24, right: 424, width: 400, height: 180, x: 24, y: 0, toJSON: () => ({}) };
      }
      return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) };
    });

    try {
      render(<div className="feed"><LiveActivityLine text={thinking.title} thinking={thinking} /></div>);
      fireEvent.click(screen.getByRole("button"));
      const dialog = screen.getByRole("dialog", { name: "思考过程" });
      expect(dialog).toHaveAttribute("data-placement", "below");

      const status = screen.getByRole("status");
      status.setAttribute("data-test-pos", "bottom");
      fireEvent.scroll(status.closest(".feed")!);
      expect(dialog).toHaveAttribute("data-placement", "above");
    } finally {
      rectSpy.mockRestore();
    }
  });

  it("shows the first replacement at once, then holds each text for at least 1.2s", () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    const { rerender } = render(<LiveActivityLine text="正在思考" />);
    rerender(<LiveActivityLine text="第一条" />);
    expect(screen.getByRole("status")).toHaveTextContent("第一条");
    act(() => { vi.advanceTimersByTime(300); });
    rerender(<LiveActivityLine text="第二条" />);
    rerender(<LiveActivityLine text="第三条" />);
    expect(screen.getByRole("status")).toHaveTextContent("第一条");
    act(() => { vi.advanceTimersByTime(900); });
    expect(screen.getByRole("status")).toHaveTextContent("第三条");
  });
});
