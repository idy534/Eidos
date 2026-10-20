import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { InputPicker } from "./InputPicker.js";
import type { EidosRuntimeAPI } from "../contracts.js";

interface Box { top: number; left: number; right: number; bottom: number }

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const heightDescriptor = Object.getOwnPropertyDescriptor(window, "innerHeight");

function stubRect(element: Element, box: Box) {
  element.getBoundingClientRect = () => ({
    x: box.left,
    y: box.top,
    top: box.top,
    left: box.left,
    right: box.right,
    bottom: box.bottom,
    width: box.right - box.left,
    height: box.bottom - box.top,
    toJSON: () => box,
  }) as DOMRect;
}

describe("InputPicker placement", () => {
  let canvas: HTMLDivElement;
  let composer: HTMLFormElement;
  const anchor: { current: HTMLElement | null } = { current: null };

  function panel(): HTMLElement {
    const element = document.querySelector<HTMLElement>(".input-picker");
    if (!element) throw new Error("InputPicker did not render");
    return element;
  }

  beforeEach(() => {
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      writable: true,
      value: {
        listSkills: vi.fn().mockResolvedValue({ skills: [] }),
        listPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
        listMcpServers: vi.fn().mockResolvedValue({ servers: [] }),
        listSessions: vi.fn().mockResolvedValue({ items: [] }),
        listWorkspaceDirectory: vi.fn().mockResolvedValue({ path: ".", entries: [], truncated: false }),
        pickInputPaths: vi.fn().mockResolvedValue([]),
      } as unknown as EidosRuntimeAPI,
    });
    Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: 620 });
    canvas = document.createElement("div");
    canvas.className = "session-canvas session-canvas--welcome";
    composer = document.createElement("form");
    composer.className = "composer";
    canvas.append(composer);
    document.body.append(canvas);
    anchor.current = composer;
  });

  afterEach(() => {
    cleanup();
    canvas.remove();
    anchor.current = null;
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
    if (heightDescriptor) Object.defineProperty(window, "innerHeight", heightDescriptor);
  });

  async function renderPicker() {
    render(
      <InputPicker
        mode="all"
        query=""
        inline={false}
        anchor={anchor}
        onQuery={() => {}}
        onChoose={() => {}}
        onClose={() => {}}
        onCommand={() => {}}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
  }

  it("renders in the viewport layer and fits above a mid-screen welcome composer", async () => {
    stubRect(canvas, { top: 44, bottom: 600, left: 0, right: 880 });
    stubRect(composer, { top: 330, bottom: 430, left: 28, right: 858 });

    await renderPicker();

    const element = panel();
    expect(element.parentElement).toBe(document.body);
    // 400px would cross the canvas top edge at 44px: the panel shrinks and keeps every row visible.
    expect(element.dataset.placement).toBe("above");
    expect(element.style.top).toBe("52px");
    expect(element.style.left).toBe("28px");
    expect(element.style.width).toBe("390px");
    expect(element.style.height).toBe("268px");
    expect(screen.getByRole("button", { name: "添加文件" })).toBeVisible();
    expect(screen.getByRole("button", { name: "添加文件夹" })).toBeVisible();
  });

  it("flips below the composer when the space above is too small", async () => {
    stubRect(canvas, { top: 44, bottom: 600, left: 0, right: 880 });
    stubRect(composer, { top: 160, bottom: 240, left: 28, right: 858 });

    await renderPicker();

    const element = panel();
    expect(element.dataset.placement).toBe("below");
    expect(element.style.top).toBe("250px");
    expect(element.style.height).toBe("342px");
  });

  it("re-measures when the window resizes", async () => {
    stubRect(canvas, { top: 44, bottom: 600, left: 0, right: 880 });
    stubRect(composer, { top: 330, bottom: 430, left: 28, right: 858 });
    await renderPicker();
    const element = panel();
    expect(element.style.top).toBe("52px");

    act(() => {
      stubRect(canvas, { top: 44, bottom: 700, left: 0, right: 880 });
      stubRect(composer, { top: 500, bottom: 600, left: 28, right: 858 });
      window.dispatchEvent(new Event("resize"));
    });

    expect(element.style.top).toBe("118px");
    expect(element.style.height).toBe("372px");
  });

  it("keeps the panel inside a narrow canvas", async () => {
    stubRect(canvas, { top: 44, bottom: 600, left: 0, right: 300 });
    stubRect(composer, { top: 330, bottom: 430, left: 28, right: 290 });

    await renderPicker();

    const element = panel();
    expect(element.style.width).toBe("284px");
    expect(element.style.left).toBe("8px");
  });

  it("fills the canvas instead of collapsing when neither side has room", async () => {
    stubRect(canvas, { top: 44, bottom: 200, left: 0, right: 880 });
    stubRect(composer, { top: 100, bottom: 180, left: 28, right: 858 });

    await renderPicker();

    const element = panel();
    expect(element.style.top).toBe("52px");
    expect(element.style.height).toBe("140px");
  });
});
