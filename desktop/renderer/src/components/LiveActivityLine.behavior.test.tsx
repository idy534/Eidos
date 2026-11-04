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

  it("closes the popover when the summary body goes away", () => {
    const { rerender } = render(<LiveActivityLine text={thinking.title} thinking={thinking} />);
    fireEvent.click(screen.getByRole("button"));
    rerender(<LiveActivityLine text="正在运行 ls" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
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
