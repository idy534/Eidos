import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceExplorer } from "./WorkspaceExplorer.js";

const shikiMock = vi.hoisted(() => ({ codeToHtml: vi.fn() }));
vi.mock("shiki/bundle/web", () => shikiMock);

function highlightedCode(code: string): string {
  const lines = code.split("\n");
  if (lines.length > 1 && lines.at(-1) === "") lines.pop();
  return `<pre class="shiki"><code>${lines.map((line, index) => (
    `<span class="line" data-source-line="${index + 1}"><span>${line}</span></span>`
  )).join("\n")}</code></pre>`;
}

beforeEach(() => {
  shikiMock.codeToHtml.mockReset();
  shikiMock.codeToHtml.mockImplementation(async (code: string) => highlightedCode(code));
});


describe("WorkspaceExplorer", () => {
  it("loads folders lazily and previews a file with one click", async () => {
    const listDirectory = vi.fn(async (_sessionId: string, path: string) => (
      path === "."
        ? {
            path: ".",
            entries: [{ name: "docs", relativePath: "docs", kind: "directory" as const }],
            truncated: false,
          }
        : {
            path: "docs",
            entries: [{
              name: "README.md",
              relativePath: "docs/README.md",
              kind: "file" as const,
              sizeBytes: 8,
            }],
            truncated: false,
          }
    ));
    const readPreview = vi.fn(async () => ({
      path: "docs/README.md",
      kind: "markdown" as const,
      sizeBytes: 8,
      truncated: false,
      content: "# Hello\n",
    }));

    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
      />,
    );

    expect(await screen.findByText("docs")).toBeInTheDocument();
    expect(listDirectory).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "展开 docs" }));
    expect(await screen.findByText("README.md")).toBeInTheDocument();
    expect(listDirectory).toHaveBeenCalledWith("session-a", "docs");
    fireEvent.click(screen.getByText("README.md"));

    expect(await screen.findByRole("heading", { name: "Hello" })).toBeInTheDocument();
    expect(readPreview).toHaveBeenCalledWith("session-a", "docs/README.md");
  });

  it("uses one compact preview bar without redundant file headings or metadata", async () => {
    const listDirectory = vi.fn(async () => ({
      path: ".",
      entries: [{
        name: "sample.test.js",
        relativePath: "tests/sample.test.js",
        kind: "file" as const,
        sizeBytes: 143,
      }],
      truncated: false,
    }));
    const readPreview = vi.fn(async () => ({
      path: "tests/sample.test.js",
      kind: "code" as const,
      sizeBytes: 143,
      truncated: false,
      content: "const test = require(\"node:test\");",
      language: "javascript",
    }));
    const { container } = render(
      <WorkspaceExplorer
        sessionId="session-a"
        layout="side"
        listDirectory={listDirectory}
        readPreview={readPreview}
      />,
    );

    fireEvent.click(await screen.findByText("sample.test.js"));
    expect(await screen.findByRole("tab", { name: "tests/sample.test.js" })).toBeInTheDocument();
    expect(screen.queryByText("tests/sample.test.js")).not.toBeInTheDocument();
    expect(screen.queryByText("143 B")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "刷新" })).not.toBeInTheDocument();
    expect(screen.queryByText("当前文件 · 143 bytes")).not.toBeInTheDocument();
    expect(screen.queryByText("提出修改意见")).not.toBeInTheDocument();
    expect(screen.queryByText("Files")).not.toBeInTheDocument();
    expect(container.querySelector(".workspace-preview-bar")).toBeInTheDocument();
    expect(container.querySelector(".workspace-preview-header")).not.toBeInTheDocument();
  });

  it("shows distinct icons for common file formats and a generic fallback", async () => {
    const listDirectory = vi.fn(async () => ({
      path: ".",
      entries: [
        { name: "app.js", relativePath: "app.js", kind: "file" as const, sizeBytes: 8 },
        { name: "main.go", relativePath: "main.go", kind: "file" as const, sizeBytes: 8 },
        { name: "README.md", relativePath: "README.md", kind: "file" as const, sizeBytes: 8 },
        { name: "tool.py", relativePath: "tool.py", kind: "file" as const, sizeBytes: 8 },
        { name: "notes.txt", relativePath: "notes.txt", kind: "file" as const, sizeBytes: 8 },
        { name: "slides.pptx", relativePath: "slides.pptx", kind: "file" as const, sizeBytes: 8 },
        { name: "table.xlsx", relativePath: "table.xlsx", kind: "file" as const, sizeBytes: 8 },
        { name: "unknown.custom", relativePath: "unknown.custom", kind: "file" as const, sizeBytes: 8 },
      ],
      truncated: false,
    }));
    const { container } = render(
      <WorkspaceExplorer sessionId="session-a" listDirectory={listDirectory} />,
    );

    await screen.findByText("app.js");
    expect(container.querySelector('[data-file-icon="javascript"]')).toBeInTheDocument();
    expect(container.querySelector('[data-file-icon="go"]')).toBeInTheDocument();
    expect(container.querySelector('[data-file-icon="markdown"]')).toBeInTheDocument();
    expect(container.querySelector('[data-file-icon="python"]')).toBeInTheDocument();
    expect(container.querySelector('[data-file-icon="text"]')).toBeInTheDocument();
    expect(container.querySelector('[data-file-icon="presentation"]')).toBeInTheDocument();
    expect(container.querySelector('[data-file-icon="table"]')).toBeInTheDocument();
    expect(container.querySelector('[data-file-icon="generic"]')).toBeInTheDocument();
  });

  it("keeps multiple opened files as preview tabs", async () => {
    const listDirectory = vi.fn(async () => ({
      path: ".",
      entries: [
        { name: "one.md", relativePath: "one.md", kind: "file" as const, sizeBytes: 8 },
        { name: "two.md", relativePath: "two.md", kind: "file" as const, sizeBytes: 8 },
      ],
      truncated: false,
    }));
    const readPreview = vi.fn(async (_sessionId: string, path: string) => ({
      path,
      kind: "markdown" as const,
      sizeBytes: 8,
      truncated: false,
      content: `# ${path}`,
    }));

    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
      />,
    );

    fireEvent.click(await screen.findByText("one.md"));
    expect(await screen.findByRole("heading", { name: "one.md" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("two.md"));
    expect(await screen.findByRole("heading", { name: "two.md" })).toBeInTheDocument();

    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(screen.getByRole("tab", { name: "one.md" }).querySelector('[data-file-icon="markdown"]')).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "two.md" }).querySelector('[data-file-icon="markdown"]')).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "one.md" }));
    expect(await screen.findByRole("heading", { name: "one.md" })).toBeInTheDocument();
  });

  it("shows typed unavailable and truncated states", async () => {
    const listDirectory = vi.fn(async () => ({
      path: ".",
      entries: [{
        name: "archive.zip",
        relativePath: "archive.zip",
        kind: "file" as const,
        sizeBytes: 20,
      }],
      truncated: true,
    }));
    const readPreview = vi.fn(async () => ({
      path: "archive.zip",
      kind: "unavailable" as const,
      sizeBytes: 20,
      truncated: false,
      reason: "unsupported" as const,
    }));

    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
      />,
    );

    expect(await screen.findByText("目录内容已截断")).toBeInTheDocument();
    fireEvent.click(screen.getByText("archive.zip"));
    await waitFor(() => {
      expect(screen.getByText("此文件类型暂不支持预览")).toBeInTheDocument();
    });
  });

  it("reloads the execution root and clears the selected file when executionKey changes", async () => {
    const listDirectory = vi.fn(async () => ({
      path: ".",
      entries: [{
        name: "README.md",
        relativePath: "README.md",
        kind: "file" as const,
        sizeBytes: 8,
      }],
      truncated: false,
    }));
    const readPreview = vi.fn(async () => ({
      path: "README.md",
      kind: "markdown" as const,
      sizeBytes: 8,
      truncated: false,
      content: "# Root A\n",
    }));
    const onSelectedFileChange = vi.fn();
    const { rerender } = render(
      <WorkspaceExplorer
        sessionId="session-a"
        executionKey="local:/workspace-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
        onSelectedFileChange={onSelectedFileChange}
      />,
    );

    fireEvent.click(await screen.findByText("README.md"));
    expect(await screen.findByRole("heading", { name: "Root A" })).toBeInTheDocument();
    expect(onSelectedFileChange).toHaveBeenLastCalledWith("README.md");

    rerender(
      <WorkspaceExplorer
        sessionId="session-a"
        executionKey="worktree:/workspace-b"
        listDirectory={listDirectory}
        readPreview={readPreview}
        onSelectedFileChange={onSelectedFileChange}
      />,
    );

    await waitFor(() => expect(listDirectory).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("heading", { name: "Root A" })).not.toBeInTheDocument();
    expect(onSelectedFileChange).toHaveBeenLastCalledWith(undefined);
  });

  it("exposes side and expanded layouts to the workspace dock", async () => {
    const listDirectory = vi.fn(async () => ({ path: ".", entries: [], truncated: false }));
    const { container, rerender } = render(
      <WorkspaceExplorer sessionId="session-a" layout="side" listDirectory={listDirectory} />,
    );
    await screen.findByText("Workspace 中没有可显示的文件");
    expect(container.querySelector(".workspace-explorer")).toHaveClass("workspace-explorer--side");
    const sideSplitter = screen.getByRole("separator", { name: "调整文件树大小" });
    expect(sideSplitter).toHaveAttribute("aria-valuemin", "88");
    expect(sideSplitter).toHaveAttribute("aria-valuemax", "512");
    expect(sideSplitter).toHaveAttribute("aria-valuenow", "88");
    fireEvent.pointerDown(sideSplitter, { clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(sideSplitter, { clientY: 120, pointerId: 1 });
    fireEvent.pointerUp(sideSplitter, { clientY: 120, pointerId: 1 });
    expect(container.querySelector(".workspace-explorer")?.getAttribute("style"))
      .toContain("--workspace-tree-size");

    rerender(
      <WorkspaceExplorer sessionId="session-a" layout="expanded" listDirectory={listDirectory} />,
    );
    expect(container.querySelector(".workspace-explorer")).toHaveClass("workspace-explorer--expanded");
    const expandedSplitter = screen.getByRole("separator", { name: "调整文件树大小" });
    expect(expandedSplitter).toHaveAttribute("aria-orientation", "vertical");
    fireEvent.keyDown(expandedSplitter, { key: "ArrowLeft" });
    expect(container.querySelector(".workspace-explorer")?.getAttribute("style"))
      .toContain("--workspace-tree-size");
  });

  it("measures the file tree after the async root listing mounts it", async () => {
    const previousResizeObserver = globalThis.ResizeObserver;
    const hadResizeObserver = "ResizeObserver" in globalThis;
    const getBoundingClientRectSpy = vi.spyOn(
      HTMLElement.prototype,
      "getBoundingClientRect",
    ).mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("workspace-tree-scroll")) {
        return {
          bottom: 144,
          height: 144,
          left: 0,
          right: 320,
          top: 0,
          width: 320,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect;
      }
      return new DOMRect();
    });

    class TestResizeObserver {
      constructor(_callback: ResizeObserverCallback) {}

      observe(_target: Element): void {}

      disconnect(): void {}
    }

    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: TestResizeObserver,
    });

    try {
      const listDirectory = vi.fn(async () => ({
        path: ".",
        entries: [{
          name: "README.md",
          relativePath: "README.md",
          kind: "file" as const,
          sizeBytes: 8,
        }],
        truncated: false,
      }));
      const { container } = render(
        <WorkspaceExplorer
          sessionId="session-a"
          layout="side"
          listDirectory={listDirectory}
        />,
      );

      await screen.findByText("README.md");
      await waitFor(() => {
        expect(container.querySelector('[role="tree"]')).toHaveStyle({ height: "144px" });
      });
    } finally {
      getBoundingClientRectSpy.mockRestore();
      if (hadResizeObserver) {
        Object.defineProperty(globalThis, "ResizeObserver", {
          configurable: true,
          value: previousResizeObserver,
        });
      } else {
        Reflect.deleteProperty(globalThis, "ResizeObserver");
      }
    }
  });

  it("refreshes only the affected loaded subtree after watcher invalidation", async () => {
    let notify: ((paths: string[]) => void) | undefined;
    const listDirectory = vi.fn(async (_sessionId: string, path: string) => ({
      path,
      entries: path === "."
        ? [{ name: "src", relativePath: "src", kind: "directory" as const }]
        : [{ name: "index.ts", relativePath: "src/index.ts", kind: "file" as const }],
      truncated: false,
    }));
    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={vi.fn()}
        subscribeChanges={(_sessionId, callback) => {
          notify = callback;
          return () => undefined;
        }}
      />,
    );

    expect(await screen.findByText("src")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开 src" }));
    expect(await screen.findByText("index.ts")).toBeInTheDocument();
    const callsBefore = listDirectory.mock.calls.length;
    notify?.(["src/index.ts"]);
    await waitFor(() => expect(listDirectory.mock.calls.length).toBe(callsBefore + 1));
    expect(listDirectory).toHaveBeenLastCalledWith("session-a", "src");
  });

  it("opens the requested file preview immediately when openRequest is provided", async () => {
    const listDirectory = vi.fn(async () => ({
      path: ".",
      entries: [
        { name: "sunset.js", relativePath: "sunset.js", kind: "file" as const, sizeBytes: 50 },
        { name: "other.js", relativePath: "other.js", kind: "file" as const, sizeBytes: 20 },
      ],
      truncated: false,
    }));
    const readPreview = vi.fn(async () => ({
      path: "sunset.js",
      kind: "code" as const,
      sizeBytes: 50,
      truncated: false,
      content: "console.log('sunset');",
      language: "javascript",
    }));

    const { rerender, container } = render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
        openRequest={{ path: "sunset.js", requestId: 1 }}
      />,
    );

    await waitFor(() => expect(container.querySelector(".workspace-code-preview"))
      .toHaveTextContent("console.log('sunset');"));
    expect(readPreview).toHaveBeenCalledWith("session-a", "sunset.js");

    const readPreview2 = vi.fn(async () => ({
      path: "other.js",
      kind: "code" as const,
      sizeBytes: 20,
      truncated: false,
      content: "console.log('other');",
      language: "javascript",
    }));
    rerender(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview2}
        openRequest={{ path: "other.js", requestId: 2 }}
      />,
    );
    await waitFor(() => expect(readPreview2).toHaveBeenCalledWith("session-a", "other.js"));
    await waitFor(() => expect(container.querySelector(".workspace-code-preview"))
      .toHaveTextContent("console.log('other');"));
  });

  it("repositions and selects the requested column when the same file is opened again", async () => {
    const listDirectory = vi.fn(async () => ({ path: ".", entries: [], truncated: false }));
    const readPreview = vi.fn(async () => ({
      path: "src/target.txt",
      kind: "text" as const,
      sizeBytes: 16,
      truncated: false,
      content: "alpha\nbeta\ngamma",
    }));
    const scrolled: HTMLElement[] = [];
    const originalRangeRect = Object.getOwnPropertyDescriptor(Range.prototype, "getBoundingClientRect");
    const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
    Object.defineProperty(Range.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({ left: 0, right: 0 }),
    });
    const scrollIntoView = vi.fn(function (this: HTMLElement) {
      scrolled.push(this);
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    try {
      const { rerender } = render(
        <WorkspaceExplorer
          sessionId="session-a"
          listDirectory={listDirectory}
          readPreview={readPreview}
          openRequest={{ path: "src/target.txt", requestId: 1, line: 2, column: 2 }}
        />,
      );

      await waitFor(() => expect(window.getSelection()?.toString()).toBe("e"));
      expect(scrolled.at(-1)).toHaveClass("preview-line");

      rerender(
        <WorkspaceExplorer
          sessionId="session-a"
          listDirectory={listDirectory}
          readPreview={readPreview}
          openRequest={{ path: "src/target.txt", requestId: 2, line: 3, column: 3 }}
        />,
      );

      await waitFor(() => expect(window.getSelection()?.toString()).toBe("m"));
      expect(scrolled).toHaveLength(2);
      expect(readPreview).toHaveBeenCalledTimes(1);
    } finally {
      if (originalRangeRect) Object.defineProperty(Range.prototype, "getBoundingClientRect", originalRangeRect);
      else Reflect.deleteProperty(Range.prototype, "getBoundingClientRect");
      if (originalScrollIntoView) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalScrollIntoView);
    }
  });

  it("reapplies line navigation after asynchronous code highlighting", async () => {
    let finishHighlight: ((html: string) => void) | undefined;
    shikiMock.codeToHtml.mockImplementationOnce(() => new Promise<string>((resolve) => {
      finishHighlight = resolve;
    }));
    const code = "alpha\nbeta\ngamma";
    const readPreview = vi.fn(async () => ({
      path: "src/target.ts",
      kind: "code" as const,
      sizeBytes: code.length,
      truncated: false,
      content: code,
      language: "typescript",
    }));
    const { container } = render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={vi.fn(async () => ({ path: ".", entries: [], truncated: false }))}
        readPreview={readPreview}
        openRequest={{ path: "src/target.ts", requestId: 1, line: 2 }}
      />,
    );

    await waitFor(() => expect(finishHighlight).toBeTypeOf("function"));
    await waitFor(() => expect(window.getSelection()?.toString()).toBe("beta"));
    window.getSelection()?.removeAllRanges();

    await act(async () => {
      finishHighlight?.(highlightedCode(code));
      await Promise.resolve();
    });

    await waitFor(() => expect(container.querySelector(".workspace-code-preview")).toBeInTheDocument());
    await waitFor(() => expect(window.getSelection()?.toString()).toBe("beta"));
    expect(container.querySelector('[data-source-line="2"]')).toBeInTheDocument();
  });

  it("maps a Markdown source line to its rendered block", async () => {
    const scrollTargets: HTMLElement[] = [];
    const originalScrollIntoView = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(function (this: HTMLElement) {
        scrollTargets.push(this);
      }),
    });
    try {
      render(
        <WorkspaceExplorer
          sessionId="session-a"
          listDirectory={vi.fn(async () => ({ path: ".", entries: [], truncated: false }))}
          readPreview={vi.fn(async () => ({
            path: "docs/README.md",
            kind: "markdown" as const,
            sizeBytes: 30,
            truncated: false,
            content: "# Intro\n\ntext\n\n## Target\n",
          }))}
          openRequest={{ path: "docs/README.md", requestId: 1, line: 5 }}
        />,
      );

      const target = await screen.findByRole("heading", { name: "Target" });
      await waitFor(() => expect(scrollTargets.at(-1)).toBe(target));
      expect(target).toHaveAttribute("data-source-line", "5");
    } finally {
      if (originalScrollIntoView) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalScrollIntoView);
    }
  });

  it("reports when a requested line is outside a truncated preview", async () => {
    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={vi.fn(async () => ({ path: ".", entries: [], truncated: false }))}
        readPreview={vi.fn(async () => ({
          path: "src/large.txt",
          kind: "text" as const,
          sizeBytes: 20,
          truncated: true,
          content: "first\nsecond",
        }))}
        openRequest={{ path: "src/large.txt", requestId: 1, line: 5 }}
      />,
    );

    expect(await screen.findByText("目标行超出了当前截断预览。")).toBeInTheDocument();
  });

  it("previews a requested file even when its parent cannot be listed", async () => {
    const listDirectory = vi.fn(async (_sessionId: string, path: string) => {
      if (path === "old") {
        throw new Error("EIDOS_RUNTIME_ERROR:WORKSPACE_BOUNDARY_VIOLATION");
      }
      return {
        path,
        entries: [{ name: "current.ts", relativePath: "current.ts", kind: "file" as const, sizeBytes: 12 }],
        truncated: false,
      };
    });
    const readPreview = vi.fn(async () => ({
      path: "old/present.ts",
      kind: "code" as const,
      sizeBytes: 12,
      truncated: false,
      content: "const present = true;",
      language: "typescript",
    }));

    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
        openRequest={{ path: "old/present.ts", requestId: 3 }}
      />,
    );

    await waitFor(() => expect(readPreview).toHaveBeenCalledWith("session-a", "old/present.ts"));
    expect(listDirectory).not.toHaveBeenCalledWith("session-a", "old");
    expect(await screen.findByRole("tab", { name: "old/present.ts" })).toBeInTheDocument();
  });

  it("rejects an absolute requested path before requesting its preview", async () => {
    const listDirectory = vi.fn(async () => ({ path: ".", entries: [], truncated: false }));
    const readPreview = vi.fn(async () => {
      throw new Error("absolute path must not be previewed");
    });

    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
        openRequest={{ path: "/Users/xielei/Documents/test1/code-test/package.json", requestId: 4 }}
      />,
    );

    expect(await screen.findByText("文件路径无效。请从当前文件树重新选择。"))
      .toBeInTheDocument();
    expect(readPreview).not.toHaveBeenCalled();
  });

  it("shows safe guidance when a current file disappears before preview", async () => {
    const listDirectory = vi.fn(async () => ({
      path: ".",
      entries: [{ name: "gone.ts", relativePath: "gone.ts", kind: "file" as const, sizeBytes: 12 }],
      truncated: false,
    }));
    const readPreview = vi.fn(async () => {
      throw new Error("EIDOS_RUNTIME_ERROR:WORKSPACE_FILE_NOT_FOUND");
    });

    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
      />,
    );

    fireEvent.click(await screen.findByText("gone.ts"));
    expect(await screen.findByText("文件不存在或已被移除。请从当前文件树重新选择。"))
      .toBeInTheDocument();
  });
});
