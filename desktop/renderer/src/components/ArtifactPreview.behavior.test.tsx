import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EidosRuntimeAPI, Item, WorkspaceFilePreview } from "../contracts.js";
import { ArtifactProvider } from "./ArtifactContext.js";
import { BrowserPanel, resolveBrowserTarget } from "./BrowserPanel.js";
import { HunkActions } from "./HunkActions.js";
import { LastTurnChanges } from "./LastTurnChanges.js";
import { MarkdownContent } from "./MarkdownContent.js";
import { ResultFiles, toolFilePaths } from "./ResultFiles.js";
import { WorkspaceExplorer } from "./WorkspaceExplorer.js";


const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const patch = [
  "diff --git a/src/index.ts b/src/index.ts",
  "--- a/src/index.ts",
  "+++ b/src/index.ts",
  "@@ -1 +1 @@",
  "-old",
  "+new",
  "",
].join("\n");

function changeItem(overrides: Partial<Item> = {}): Item {
  return {
    id: "item-change",
    sessionId: "session-a",
    runId: "run-a",
    ordinal: 1,
    kind: "file_change",
    status: "completed",
    createdAt: 1,
    ...overrides,
    toolCall: {
      id: "tool-a",
      itemId: "item-change",
      modelStepIndex: 1,
      batchOrder: 0,
      providerCallId: "provider-a",
      toolName: "apply_patch",
      status: "completed",
      startedAt: 1,
      completedAt: 2,
      changeDiff: patch,
      baseSha256: "b".repeat(64),
      ...(overrides.toolCall ?? {}),
    },
  };
}

function artifactValue() {
  return {
    sessionId: "session-a",
    executionRoot: "/workspace",
    openFile: vi.fn(),
    openBrowser: vi.fn(),
  };
}

describe("artifact previews and feedback", () => {
  let api: Partial<EidosRuntimeAPI>;

  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
    vi.stubGlobal("MutationObserver", class {
      observe() {}
      disconnect() {}
    });
    vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValue("operation-1") });
    api = {
      onNotification: vi.fn().mockReturnValue(vi.fn()),
      prepareWorkspacePreview: vi.fn().mockImplementation(async (_sessionId: string, path: string) => `eidos-preview://preview/${path}`),
      releaseWorkspacePreview: vi.fn().mockResolvedValue(undefined),
      openBrowser: vi.fn().mockResolvedValue({ url: "http://localhost:3000", title: "App", loading: false }),
      closeBrowser: vi.fn().mockResolvedValue(undefined),
      setBrowserBounds: vi.fn().mockResolvedValue(undefined),
      readBrowserState: vi.fn().mockResolvedValue({ url: "http://localhost:3000", title: "App", loading: false }),
      annotateBrowser: vi.fn().mockResolvedValue({
        url: "eidos-preview://preview/docs/index.html",
        title: "Preview",
        selection: "selected text",
        screenshot: "data:image/png;base64,AAAA",
        capturedAt: 1_700_000_000_000,
        elements: [{ tag: "button", text: "Save", x: 0, y: 0, width: 20, height: 10 }],
      }),
      readGitReviewPatch: vi.fn().mockResolvedValue({ patch, diffHash: "d".repeat(64), head: "h".repeat(40) }),
      applyGitHunk: vi.fn().mockResolvedValue({}),
      readSession: vi.fn().mockResolvedValue({ items: [], runs: [], stepResolutions: [], session: {} }),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("resolves local Markdown artifacts and routes web links to the right owner", async () => {
    const value = artifactValue();
    render(
      <ArtifactProvider value={value}>
        <MarkdownContent
          documentPath="docs/README.md"
          content={'[report](report.pdf)\n\n![diagram](assets/diagram.png)\n\n[site](https://example.com)\n\n[unsafe](javascript:alert(1))\n\n[outside](../../secret.txt)'}
        />
      </ArtifactProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: "report" }));
    expect(value.openFile).toHaveBeenCalledWith("docs/report.pdf");
    fireEvent.click(screen.getByRole("link", { name: "site" }));
    expect(value.openBrowser).toHaveBeenCalledWith("https://example.com");

    expect(await screen.findByAltText("diagram")).toBeInTheDocument();
    expect(api.prepareWorkspacePreview).toHaveBeenCalledWith(
      "session-a", "docs/assets/diagram.png", undefined,
    );
    fireEvent.click(screen.getByRole("button", { name: "打开 diagram" }));
    expect(value.openFile).toHaveBeenLastCalledWith("docs/assets/diagram.png");
    expect(screen.queryByRole("link", { name: "unsafe" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "outside" })).not.toBeInTheDocument();
  });

  it("collects persisted result files while retaining the incomplete warning", () => {
    const item = changeItem({
      toolCall: {
        id: "tool-a",
        itemId: "item-change",
        modelStepIndex: 1,
        batchOrder: 0,
        providerCallId: "provider-a",
        toolName: "apply_patch",
        status: "completed",
        startedAt: 1,
        changeDiff: patch,
        resultJson: JSON.stringify({ data: { created: ["generated.txt"], deleted: ["gone.txt"] } }),
      },
    });
    expect(toolFilePaths(item.toolCall!)).toEqual(["src/index.ts", "generated.txt", "gone.txt"]);

    const value = artifactValue();
    render(
      <ArtifactProvider value={value}>
        <ResultFiles items={[item]} incomplete />
      </ArtifactProvider>,
    );

    expect(screen.getByText("结果文件 · 3")).toBeInTheDocument();
    expect(screen.getByText("更早的记录尚未加载，完整目录见下方文件树。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "generated.txt" }));
    expect(value.openFile).toHaveBeenCalledWith("generated.txt");
  });

  it("previews versioned image, PDF, and HTML assets through the Workspace Explorer", async () => {
    const previews: Record<string, WorkspaceFilePreview> = {
      "diagram.png": { path: "diagram.png", kind: "image", sizeBytes: 4, truncated: false, version: "a".repeat(64), mimeType: "image/png" },
      "manual.pdf": { path: "manual.pdf", kind: "pdf", sizeBytes: 4, truncated: false, version: "b".repeat(64), mimeType: "application/pdf" },
      "page.html": { path: "page.html", kind: "html", sizeBytes: 4, truncated: false, version: "c".repeat(64), mimeType: "text/html", content: "<p>preview</p>" },
    };
    const listDirectory = vi.fn().mockResolvedValue({
      path: ".",
      entries: Object.keys(previews).map((path) => ({ name: path, relativePath: path, kind: "file" as const, sizeBytes: 4 })),
      truncated: false,
    });
    const readPreview = vi.fn(async (_sessionId: string, path: string) => previews[path]!);
    const value = artifactValue();
    render(
      <ArtifactProvider value={value}>
        <WorkspaceExplorer sessionId="session-a" listDirectory={listDirectory} readPreview={readPreview} />
      </ArtifactProvider>,
    );

    fireEvent.click(await screen.findByText("diagram.png"));
    expect(await screen.findByAltText("diagram.png")).toBeInTheDocument();
    expect(api.prepareWorkspacePreview).toHaveBeenCalledWith("session-a", "diagram.png", "a".repeat(64));

    fireEvent.click(screen.getByText("manual.pdf"));
    expect(await screen.findByTitle("manual.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByText("page.html"));
    expect(await screen.findByText("你可以在网页面板中操作页面并检查效果。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开交互预览" }));
    expect(value.openBrowser).toHaveBeenLastCalledWith("eidos-preview://preview/page.html");
  });

  it("shows the last-turn patch and sends feedback with an exact tool anchor", async () => {
    const value = artifactValue();
    const onFeedback = vi.fn().mockResolvedValue(undefined);
    const item = changeItem();
    const { container } = render(
      <ArtifactProvider value={value}>
        <LastTurnChanges sessionId="session-a" items={[item]} runId="run-a" disabled={false} onFeedback={onFeedback} />
      </ArtifactProvider>,
    );

    expect(screen.getByText("执行完成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "src/index.ts" }));
    const gutter = container.querySelector(".diff-gutter");
    expect(gutter).not.toBeNull();
    fireEvent.click(gutter!);
    fireEvent.change(screen.getByRole("textbox", { name: "本轮修改反馈" }), { target: { value: "请检查这一行" } });
    fireEvent.click(screen.getByRole("button", { name: "发送反馈" }));

    await waitFor(() => expect(onFeedback).toHaveBeenCalledWith(expect.stringContaining("Run: run-a")));
    expect(onFeedback).toHaveBeenCalledWith(expect.stringContaining("ToolCall: tool-a"));
    expect(onFeedback).toHaveBeenCalledWith(expect.stringContaining("Base SHA:"));
    expect(value.openFile).toHaveBeenCalledWith("src/index.ts");
  });

  it("loads and applies a selected Git hunk with a new operation id", async () => {
    const onChanged = vi.fn();
    const applyGitHunk = api.applyGitHunk!;
    render(<HunkActions sessionId="session-a" path="src/index.ts" layer="unstaged" disabled={false} onChanged={onChanged} />);

    fireEvent.click(screen.getByText("按修改块操作 · 未暂存"));
    fireEvent.click(await screen.findByRole("button", { name: "暂存此块" }));

    await waitFor(() => expect(applyGitHunk).toHaveBeenCalledWith(
      "session-a",
      expect.objectContaining({
        path: "src/index.ts",
        action: "stage",
        hunkIndex: 0,
        diffHash: "d".repeat(64),
        operationId: "operation-1",
      }),
    ));
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("resolves addresses and search terms for the browser", () => {
    expect(resolveBrowserTarget("https://example.com/docs")).toBe("https://example.com/docs");
    expect(resolveBrowserTarget("example.com/docs")).toBe("https://example.com/docs");
    expect(resolveBrowserTarget("localhost:3000")).toBe("http://localhost:3000");
    expect(resolveBrowserTarget("123")).toBe("https://www.google.com/search?q=123");
    expect(resolveBrowserTarget("设计系统")).toBe("https://www.google.com/search?q=%E8%AE%BE%E8%AE%A1%E7%B3%BB%E7%BB%9F");
  });

  it("opens a page in its own browser tab and removes unsupported controls", async () => {
    const pollers: Array<() => void> = [];
    vi.spyOn(window, "setInterval").mockImplementation((handler) => {
      if (typeof handler === "function") pollers.push(handler as () => void);
      return 1;
    });
    const { unmount } = render(
      <BrowserPanel
        browserId="browser-1"
        sessionId="session-a"
        executionKey="local:/workspace"
        active
        request={{ url: "localhost:3000", id: 1 }}
      />,
    );

    await waitFor(() => expect(api.openBrowser).toHaveBeenCalledWith("session-a", "browser-1", "http://localhost:3000"));
    const address = screen.getByRole("textbox", { name: "网页地址" });
    fireEvent.change(address, { target: { value: "" } });
    expect(address).toHaveValue("");
    fireEvent.change(address, { target: { value: "example.com/docs" } });
    const poll = pollers.at(-1);
    expect(poll).toBeDefined();
    await act(async () => {
      (poll as () => void)();
      await Promise.resolve();
    });
    expect(address).toHaveValue("example.com/docs");
    expect(screen.queryByRole("button", { name: "标注" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开发终端" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "网页菜单" })).not.toBeInTheDocument();
    unmount();
    expect(api.closeBrowser).toHaveBeenCalledWith("session-a", "browser-1");
  });
});
