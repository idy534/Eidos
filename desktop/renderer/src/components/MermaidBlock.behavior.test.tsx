import { afterEach, describe, expect, it, vi } from "vitest";
import mermaid from "mermaid";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

if (typeof window !== "undefined" && window.SVGElement) {
  window.SVGElement.prototype.getBBox = function () {
    return { x: 0, y: 0, width: 100, height: 20 };
  };
  window.SVGElement.prototype.getBoundingClientRect = function () {
    return { x: 0, y: 0, width: 100, height: 20, top: 0, left: 0, right: 100, bottom: 20 };
  };
}

describe("Mermaid SVG output inspection", () => {
  it("renders gitGraph and flowchart with fixed dimensions (useMaxWidth: false)", async () => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: "neutral",
      fontFamily: "var(--font-ui)",
      flowchart: { useMaxWidth: false, rankSpacing: 28, nodeSpacing: 24, padding: 8, curve: "basis" },
      sequence: { useMaxWidth: false },
      gitGraph: { useMaxWidth: false },
      state: { useMaxWidth: false },
      class: { useMaxWidth: false },
      er: { useMaxWidth: false },
      themeVariables: { fontSize: "13px", edgeLabelBackground: "transparent" },
    });

    console.log("BODY CHILDREN BEFORE:", document.body.children.length);
    const gitGraphCode = `gitGraph
       commit id: "119ddad" type: HIGHLIGHT tag: "Initial commit"
       branch "text/aaa-0818"
       checkout "text/aaa-0818"
       commit id: "a8225ef" tag: "测试代码"
       commit id: "d4114e4" tag: "docs: README 更新"`;

    const { svg: gitGraphSvg } = await mermaid.render("test_git_graph_user", gitGraphCode);
    const gitSvgTag = gitGraphSvg.match(/<svg[^>]*>/)?.[0] ?? "";
    console.log("GITGRAPH FULL SVG:\n", gitGraphSvg);

    // width should be a pixel number, NOT width="100%"
    expect(gitSvgTag).not.toMatch(/width="100%"/);
    expect(gitSvgTag).toMatch(/width="\d+(\.\d+)?"/);

    // Image 2 from user
    const flowCode = `graph TD
        A["main · codex/123<br/>origin/main · origin/HEAD"]
        B["119ddad<br/>Initial commit<br/>2026-08-18"]
        C["a8225ef<br/>测试代码<br/>2026-08-24"]
        D["text/aaa-0818 ★HEAD<br/>origin/text/aaa-0818"]
        E["工作区（未提交）<br/>M README.md<br/>M tests/sample_test.py<br/>?? 20 个未跟踪样例文件"]
        F["d4114e4<br/>docs: 更新 README 目录结构与<br/>运行说明<br/>2026-09-20"]
        A --> B
        B --> C
        D --> F
        C --> F
        E --> F`;

    const { svg: flowSvg } = await mermaid.render("test_flow_user", flowCode);
    const flowSvgTag = flowSvg.match(/<svg[^>]*>/)?.[0] ?? "";
    console.log("USER FLOW SVG TAG:", flowSvgTag);

    expect(flowSvgTag).not.toMatch(/width="100%"/);
    expect(flowSvgTag).toMatch(/width="\d+(\.\d+)?"/);
    expect(flowSvg).toContain("工作区（未提交）");
    expect(flowSvg).toContain("?? 20 个未跟踪样例文件");
  });
});

describe("MermaidModal zoom and pan behavior", () => {
  const dummySvg = '<svg id="diagram" width="300" height="200"><rect width="300" height="200" fill="blue"/></svg>';
  const dummyCode = "graph TD\nA-->B";

  afterEach(() => {
    cleanup();
  });

  it("defaults to 100% zoom and renders zoom controls", async () => {
    const { MermaidModal } = await import("./MermaidBlock.js");
    const onClose = vi.fn();

    const { unmount } = render(<MermaidModal svg={dummySvg} code={dummyCode} onClose={onClose} />);

    const indicator = screen.getByRole("button", { name: /当前缩放/ });
    expect(indicator).toBeInTheDocument();
    expect(indicator.textContent).toBe("100%");

    const resetBtn = screen.getByRole("button", { name: "重置缩放" });
    expect(resetBtn).toBeInTheDocument();
    expect(resetBtn).toBeDisabled();

    const svgWrapper = document.querySelector(".mermaid-modal-svg-wrapper");
    expect(svgWrapper).toHaveStyle({ zoom: "1" });
    unmount();
  });

  it("supports zooming in, zooming out, and resetting zoom", async () => {
    const { MermaidModal } = await import("./MermaidBlock.js");
    const onClose = vi.fn();

    const { unmount } = render(<MermaidModal svg={dummySvg} code={dummyCode} onClose={onClose} />);

    const zoomInBtn = screen.getByRole("button", { name: "放大" });
    const zoomOutBtn = screen.getByRole("button", { name: "缩小" });
    const resetBtn = screen.getByRole("button", { name: "重置缩放" });
    const indicator = screen.getByRole("button", { name: /当前缩放/ });
    const svgWrapper = document.querySelector(".mermaid-modal-svg-wrapper");

    // Reset button is present by default and disabled at 100%
    expect(resetBtn).toBeDisabled();

    // Click Zoom In -> 125%
    fireEvent.click(zoomInBtn);
    expect(indicator.textContent).toBe("125%");
    expect(svgWrapper).toHaveStyle({ zoom: "1.25" });
    expect(resetBtn).not.toBeDisabled();

    // Click Zoom In again -> 150%
    fireEvent.click(zoomInBtn);
    expect(indicator.textContent).toBe("150%");
    expect(svgWrapper).toHaveStyle({ zoom: "1.5" });

    // Click Zoom Out -> 125%
    fireEvent.click(zoomOutBtn);
    expect(indicator.textContent).toBe("125%");
    expect(resetBtn).not.toBeDisabled();

    // Click Reset -> 100%
    fireEvent.click(resetBtn);
    expect(indicator.textContent).toBe("100%");
    expect(svgWrapper).toHaveStyle({ zoom: "1" });
    expect(resetBtn).toBeDisabled();
    unmount();
  });

  it("supports keyboard shortcuts (+, -, 0, Escape) for zoom and closing", async () => {
    const { MermaidModal } = await import("./MermaidBlock.js");
    const onClose = vi.fn();

    const { unmount } = render(<MermaidModal svg={dummySvg} code={dummyCode} onClose={onClose} />);

    const indicator = screen.getByRole("button", { name: /当前缩放/ });

    // Zoom in with '+'
    fireEvent.keyDown(window, { key: "+" });
    expect(indicator.textContent).toBe("125%");

    // Zoom out with '-'
    fireEvent.keyDown(window, { key: "-" });
    expect(indicator.textContent).toBe("100%");

    // Zoom out again to 75%
    fireEvent.keyDown(window, { key: "-" });
    expect(indicator.textContent).toBe("75%");

    // Reset with '0'
    fireEvent.keyDown(window, { key: "0" });
    expect(indicator.textContent).toBe("100%");

    // Escape closes modal
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();
  });

  it("supports wheel pinch zoom with ctrlKey", async () => {
    const { MermaidModal } = await import("./MermaidBlock.js");
    const onClose = vi.fn();

    const { unmount } = render(<MermaidModal svg={dummySvg} code={dummyCode} onClose={onClose} />);

    const modalBody = document.querySelector(".mermaid-modal-body")!;
    const indicator = screen.getByRole("button", { name: /当前缩放/ });

    // Wheel pinch-out (deltaY < 0, zooming in)
    fireEvent.wheel(modalBody, { deltaY: -100, ctrlKey: true });

    expect(indicator.textContent).toBe("110%");
    unmount();
  });

  it("supports mouse dragging to pan when interacting with modal body", async () => {
    const { MermaidModal } = await import("./MermaidBlock.js");
    const onClose = vi.fn();

    const { unmount } = render(<MermaidModal svg={dummySvg} code={dummyCode} onClose={onClose} />);

    const modalBody = document.querySelector(".mermaid-modal-body") as HTMLDivElement;
    modalBody.setPointerCapture = vi.fn();
    modalBody.releasePointerCapture = vi.fn();

    expect(modalBody.className).not.toContain("is-dragging");

    // Pointer down
    fireEvent.pointerDown(modalBody, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    expect(modalBody.className).toContain("is-dragging");
    expect(modalBody.setPointerCapture).toHaveBeenCalledWith(1);

    // Pointer move
    fireEvent.pointerMove(modalBody, { clientX: 50, clientY: 40 });

    // Pointer up
    fireEvent.pointerUp(modalBody, { pointerId: 1 });
    expect(modalBody.className).not.toContain("is-dragging");
    expect(modalBody.releasePointerCapture).toHaveBeenCalledWith(1);

    unmount();
  });
});
