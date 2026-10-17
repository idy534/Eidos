import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SessionCanvas } from "./SessionCanvas.js";

afterEach(() => vi.restoreAllMocks());

it("keeps the editable Composer mounted across the welcome transition", () => {
  const composer = <form className="composer"><input aria-label="草稿" defaultValue="未发送" /></form>;
  const { rerender } = render(<SessionCanvas sessionId="draft-a" welcome feed={<p>任务记录</p>}>{composer}</SessionCanvas>);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "继续编辑" } });
  rerender(<SessionCanvas sessionId="draft-a" welcome={false} feed={<p>任务记录</p>}>{composer}</SessionCanvas>);
  expect(screen.getByRole("textbox")).toBe(input);
  expect(input).toHaveValue("继续编辑");
  expect(screen.getByText("任务记录")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "让思考抵达现实" })).not.toBeInTheDocument();
});

it("skips the slide when the system requests reduced motion", () => {
  vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
  const animate = vi.fn();
  const { rerender, container } = render(<SessionCanvas sessionId="draft-a" welcome feed={null}>
    <form className="composer" />
  </SessionCanvas>);
  Object.defineProperty(container.querySelector(".composer"), "animate", { configurable: true, value: animate });
  rerender(<SessionCanvas sessionId="draft-a" welcome={false} feed={null}><form className="composer" /></SessionCanvas>);
  expect(animate).not.toHaveBeenCalled();
});
