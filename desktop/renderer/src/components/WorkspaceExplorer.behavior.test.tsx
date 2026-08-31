import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceExplorer } from "./WorkspaceExplorer.js";


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
    expect(sideSplitter).toHaveAttribute("aria-valuenow", "204");
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

    const { rerender } = render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
        openRequest={{ path: "sunset.js", requestId: 1 }}
      />,
    );

    expect(await screen.findByText("console.log('sunset');")).toBeInTheDocument();
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
    expect(await screen.findByText("console.log('other');")).toBeInTheDocument();
  });

  it("does not preview a stale requested path that is absent from the current workspace", async () => {
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
    const readPreview = vi.fn(async () => {
      throw new Error("stale path must not be previewed");
    });

    render(
      <WorkspaceExplorer
        sessionId="session-a"
        listDirectory={listDirectory}
        readPreview={readPreview}
        openRequest={{ path: "old/present.ts", requestId: 3 }}
      />,
    );

    expect(await screen.findByText("文件不存在或已过期。请从当前文件树重新选择。"))
      .toBeInTheDocument();
    expect(readPreview).not.toHaveBeenCalled();
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
