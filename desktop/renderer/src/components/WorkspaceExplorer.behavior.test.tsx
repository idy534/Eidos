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

    rerender(
      <WorkspaceExplorer sessionId="session-a" layout="expanded" listDirectory={listDirectory} />,
    );
    expect(container.querySelector(".workspace-explorer")).toHaveClass("workspace-explorer--expanded");
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
});
