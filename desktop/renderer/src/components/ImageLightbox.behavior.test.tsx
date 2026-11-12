import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ImageLightbox, type LightboxImage } from "./ImageLightbox.js";

const images: LightboxImage[] = [
  { name: "one.png", url: "eidos-preview://token/one.png" },
  { name: "two.png", url: "eidos-preview://token/two.png" },
  { name: "three.png", status: "暂无预览" },
];

function Gallery({ start = 0, onClose = () => undefined }: { start?: number; onClose?: () => void }) {
  const [index, setIndex] = useState(start);
  return <ImageLightbox images={images} index={index} onIndexChange={setIndex} onClose={onClose} />;
}

describe("ImageLightbox", () => {
  it("shows one image as a plain enlargement and closes on Escape, the button or the backdrop", () => {
    const onClose = vi.fn();
    render(<ImageLightbox images={[images[0]!]} onClose={onClose} />);
    const dialog = screen.getByRole("dialog", { name: "one.png 图片预览" });
    expect(screen.getByRole("img", { name: "one.png" })).toHaveAttribute("src", "eidos-preview://token/one.png");
    expect(screen.queryByRole("button", { name: "下一张" })).not.toBeInTheDocument();
    expect(screen.queryByText("1 / 1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭图片预览" })).toHaveFocus();

    // Arrow keys do nothing without a second image.
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("dialog", { name: "one.png 图片预览" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("img", { name: "one.png" }));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(dialog);
    fireEvent.click(screen.getByRole("button", { name: "关闭图片预览" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("steps through several images with the buttons and stops at both ends", () => {
    render(<Gallery />);
    expect(screen.getByRole("dialog", { name: "one.png 图片预览" })).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    const previous = screen.getByRole("button", { name: "上一张" });
    const next = screen.getByRole("button", { name: "下一张" });
    expect(previous).toHaveAttribute("aria-disabled", "true");
    expect(next).toHaveAttribute("aria-disabled", "false");

    fireEvent.click(previous);
    expect(screen.getByText("1 / 3")).toBeInTheDocument();

    fireEvent.click(next);
    expect(screen.getByRole("dialog", { name: "two.png 图片预览" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "two.png" })).toHaveAttribute("src", "eidos-preview://token/two.png");
    expect(screen.getByText("2 / 3")).toBeInTheDocument();

    fireEvent.click(next);
    // An image without a URL keeps its place in the sequence and says why.
    expect(screen.getByRole("dialog", { name: "three.png 图片预览" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("暂无预览");
    expect(next).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(next);
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
  });

  it("steps with the arrow keys and leaves modified shortcuts alone", () => {
    const onClose = vi.fn();
    render(<Gallery start={1} onClose={onClose} />);
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowRight", metaKey: true });
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByText("3 / 3")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("counts every key of a fast repeat even before the caller re-renders", () => {
    const onIndexChange = vi.fn();
    render(<ImageLightbox images={images} index={0} onIndexChange={onIndexChange} onClose={() => undefined} />);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(onIndexChange.mock.calls).toEqual([[1], [2]]);
  });

  it("uses the caller's dialog name and reports a failed image as an alert", () => {
    render(<ImageLightbox images={[{ name: "截图", dialogLabel: "截图预览", status: "无法加载原图预览。", failed: true }]} onClose={() => undefined} />);
    expect(screen.getByRole("dialog", { name: "截图预览" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("无法加载原图预览。");
  });
});
