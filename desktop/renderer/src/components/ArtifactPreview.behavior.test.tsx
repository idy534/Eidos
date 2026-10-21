import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EidosRuntimeAPI, Item, WorkspaceFilePreview } from "../contracts.js";
import { ArtifactImage, ArtifactProvider } from "./ArtifactContext.js";
import { BrowserPanel, getBrowserTabTitle, resolveBrowserTarget } from "./BrowserPanel.js";
import { LastTurnChanges } from "./LastTurnChanges.js";
import { MarkdownContent } from "./MarkdownContent.js";
import { ResultFiles, toolFilePaths } from "./ResultFiles.js";
import { WorkspaceExplorer } from "./WorkspaceExplorer.js";

const mockShikiImplementation = {
  codeToHtml: async (code: string) => {
    const parts = code.split("\n");
    if (parts.length > 1 && parts[parts.length - 1] === "") parts.pop();
    return (
      `<pre class="shiki"><code>${parts.map((line) => (
        `<span class="line"><span>${line}</span></span>`
      )).join("\n")}</code></pre>`
    );
  },
};
vi.mock("shiki", () => mockShikiImplementation);
vi.mock("shiki/bundle/web", () => mockShikiImplementation);


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

  it("shows an empty preview for a removed file and retries after the file changes", async () => {
    vi.mocked(api.prepareWorkspacePreview!).mockResolvedValueOnce(null).mockResolvedValueOnce("eidos-preview://restored/image.png");
    const { unmount } = render(<ArtifactProvider value={artifactValue()}><ArtifactImage path="image.png" alt="Image" /></ArtifactProvider>);
    expect(await screen.findByText("暂无预览")).toBeInTheDocument();
    expect(api.releaseWorkspacePreview).not.toHaveBeenCalled();
    const notify = vi.mocked(api.onNotification!).mock.calls[0]![0];
    act(() => notify({ method: "workspace/changed", params: { sessionId: "session-a", paths: ["image.png"] } }));
    expect(await screen.findByRole("img", { name: "Image" })).toHaveAttribute("src", "eidos-preview://restored/image.png");
    unmount();
    expect(api.releaseWorkspacePreview).toHaveBeenCalledWith("eidos-preview://restored/image.png");
  });

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
      stopBrowser: vi.fn().mockResolvedValue(undefined),
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
      readSession: vi.fn().mockResolvedValue({ items: [], runs: [], stepResolutions: [], session: {} }),
      openWorkspacePathInEditor: vi.fn().mockResolvedValue(undefined),
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
      "session-a", "docs/assets/diagram.png", undefined, "/workspace", undefined,
    );
    fireEvent.click(screen.getByRole("button", { name: "打开 diagram" }));
    expect(value.openFile).toHaveBeenLastCalledWith("docs/assets/diagram.png");
    expect(screen.queryByRole("link", { name: "unsafe" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "outside" })).not.toBeInTheDocument();
  });

  it("opens explicit conversation file links at their parsed position", () => {
    const value = artifactValue();
    render(
      <ArtifactProvider value={value}>
        <MarkdownContent content={[
          "[app.ts (line 42, column 5)](</workspace/My%20Project/src/app.ts:42:5>)",
          "[legacy.ts](</workspace/src/legacy.ts#L19C3>)",
          "[relative](src/relative.ts)",
          "`bare.ts`",
        ].join("\n\n")} />
      </ArtifactProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: "app.ts (line 42, column 5)" }));
    expect(value.openFile).toHaveBeenNthCalledWith(1, "My Project/src/app.ts", {
      line: 42,
      column: 5,
    });
    fireEvent.click(screen.getByRole("link", { name: "legacy.ts" }));
    expect(value.openFile).toHaveBeenNthCalledWith(2, "src/legacy.ts", {
      line: 19,
      column: 3,
    });
    expect(screen.queryByRole("link", { name: "relative" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "bare.ts" })).not.toBeInTheDocument();
    expect(screen.getByText("bare.ts").closest("code")).toBeInTheDocument();
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
    expect(api.prepareWorkspacePreview).toHaveBeenCalledWith(
      "session-a", "diagram.png", "a".repeat(64), "/workspace", undefined,
    );

    fireEvent.click(screen.getByText("manual.pdf"));
    expect(await screen.findByTitle("manual.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByText("page.html"));
    expect(await screen.findByText("你可以在网页面板中操作页面并检查效果。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开交互预览" }));
    expect(value.openBrowser).toHaveBeenLastCalledWith("eidos-preview://preview/page.html");
  });

  it("numbers plain text preview lines without a phantom trailing row", async () => {
    const previews: Record<string, WorkspaceFilePreview> = {
      "notes.txt": { path: "notes.txt", kind: "text", sizeBytes: 8, truncated: false, content: "first\n\nthird\n" },
    };
    const listDirectory = vi.fn().mockResolvedValue({
      path: ".",
      entries: [{ name: "notes.txt", relativePath: "notes.txt", kind: "file" as const, sizeBytes: 8 }],
      truncated: false,
    });
    const readPreview = vi.fn(async (_sessionId: string, path: string) => previews[path]!);
    const value = artifactValue();
    const { container } = render(
      <ArtifactProvider value={value}>
        <WorkspaceExplorer sessionId="session-a" listDirectory={listDirectory} readPreview={readPreview} />
      </ArtifactProvider>,
    );

    fireEvent.click(await screen.findByText("notes.txt"));
    const numbers = await waitFor(() => {
      const found = container.querySelectorAll(".preview-line-number");
      expect(found).toHaveLength(3);
      return found;
    });
    expect([...numbers].map((node) => node.textContent)).toEqual(["1", "2", "3"]);
    expect(container.querySelectorAll(".preview-line")).toHaveLength(3);
  });

  it("renders highlighted code with per-line spans for CSS line numbers", async () => {
    const previews: Record<string, WorkspaceFilePreview> = {
      "main.py": { path: "main.py", kind: "code", sizeBytes: 16, truncated: false, language: "python", content: "a = 1\nb = 2\n" },
    };
    const listDirectory = vi.fn().mockResolvedValue({
      path: ".",
      entries: [{ name: "main.py", relativePath: "main.py", kind: "file" as const, sizeBytes: 16 }],
      truncated: false,
    });
    const readPreview = vi.fn(async (_sessionId: string, path: string) => previews[path]!);
    const value = artifactValue();
    const { container } = render(
      <ArtifactProvider value={value}>
        <WorkspaceExplorer sessionId="session-a" listDirectory={listDirectory} readPreview={readPreview} />
      </ArtifactProvider>,
    );

    fireEvent.click(await screen.findByText("main.py"));
    await waitFor(() => {
      expect(container.querySelectorAll(".workspace-code-preview .line")).toHaveLength(2);
    });
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

    expect(screen.queryByText("执行完成")).not.toBeInTheDocument();
    expect(screen.queryByText(/这里按工具执行顺序展示文件修改记录/)).not.toBeInTheDocument();
    const fileButton = screen.getByRole("button", { name: "src/index.ts" });
    expect(fileButton).toBeInTheDocument();
    expect(fileButton).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "在工作区打开 src/index.ts" }));
    const gutter = container.querySelector(".diff-gutter");
    expect(gutter).not.toBeNull();
    fireEvent.click(gutter!);
    fireEvent.change(screen.getByRole("textbox", { name: "本轮修改反馈" }), { target: { value: "请检查这一行" } });
    fireEvent.click(screen.getByRole("button", { name: "发送反馈" }));

    await waitFor(() => expect(onFeedback).toHaveBeenCalledWith(expect.stringContaining("Run: run-a")));
    expect(onFeedback).toHaveBeenCalledWith(expect.stringContaining("ToolCall: tool-a"));
    expect(onFeedback).toHaveBeenCalledWith(expect.stringContaining("Base SHA:"));
    expect(value.openFile).toHaveBeenCalledWith("src/index.ts");
    fireEvent.click(screen.getByRole("button", { name: "在编辑器中打开 src/index.ts" }));
    await waitFor(() => expect(api.openWorkspacePathInEditor).toHaveBeenCalledWith("session-a", "src/index.ts"));
  });

  it("groups headerless runtime diffs by the recorded result path", () => {
    const value = artifactValue();
    const headerless = [
      "--- a/tests/sample_test.yaml",
      "+++ /dev/null",
      "@@ -1,2 +0,0 @@",
      "-old",
      "-gone",
      "",
    ].join("\n");
    const item = {
      ...changeItem(),
      toolCall: {
        ...changeItem().toolCall!,
        changeDiff: headerless,
        resultJson: JSON.stringify({ outcome: "success", code: "ok", data: { path: "tests/sample_test.yaml" } }),
      },
    };
    render(
      <ArtifactProvider value={value}>
        <LastTurnChanges sessionId="session-a" items={[item]} runId="run-a" disabled={false} onFeedback={vi.fn().mockResolvedValue(undefined)} />
      </ArtifactProvider>,
    );

    expect(screen.getByRole("button", { name: "tests/sample_test.yaml" })).toBeInTheDocument();
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

    await waitFor(() => expect(api.openBrowser).toHaveBeenCalledWith("session-a", "browser-1", "http://localhost:3000", undefined));
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
    expect(screen.queryByText("页面正在加载…")).not.toBeInTheDocument();
    const refresh = screen.getByRole("button", { name: "刷新" });
    fireEvent.click(refresh);
    expect(refresh.querySelector("svg")).toHaveClass("browser-navigation__refresh-icon--refreshing");
    expect(screen.queryByRole("button", { name: "标注" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "开发终端" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "网页菜单" })).not.toBeInTheDocument();
    unmount();
    expect(api.closeBrowser).toHaveBeenCalledWith("session-a", "browser-1");
  });

  it("immediately populates the address input and displays loading indicators while fetching", async () => {
    expect(getBrowserTabTitle("https://news.web.nhk/item1")).toBe("news.web.nhk");
    expect(getBrowserTabTitle("eidos-preview://token/demo.html")).toBe("demo.html");
    expect(getBrowserTabTitle("")).toBe("新标签页");

    let resolveOpen: (value: { url: string; title: string; loading: boolean }) => void = () => {};
    const openPromise = new Promise<{ url: string; title: string; loading: boolean }>((resolve) => {
      resolveOpen = resolve;
    });
    vi.mocked(api.openBrowser).mockImplementationOnce(() => openPromise);

    const onPageStateChange = vi.fn();
    const { unmount } = render(
      <BrowserPanel
        browserId="browser-2"
        sessionId="session-b"
        executionKey="local:/workspace"
        active
        request={{ url: "https://news.web.nhk/item1", id: 1 }}
        onPageStateChange={onPageStateChange}
      />,
    );

    // Immediate feedback without waiting for openBrowser to resolve:
    const address = screen.getByRole("textbox", { name: "网页地址" });
    expect(address).toHaveValue("https://news.web.nhk/item1");
    expect(screen.getByRole("progressbar", { name: "网页加载进度" })).toBeInTheDocument();
    expect(screen.getByText("正在连接并加载网页…")).toBeInTheDocument();
    expect(screen.getByText("https://news.web.nhk/item1")).toBeInTheDocument();
    const stopBtn = screen.getByRole("button", { name: "停止加载" });
    expect(stopBtn).not.toBeDisabled();
    expect(stopBtn.querySelector(".browser-navigation__stop-icon")).toBeInTheDocument();
    expect(onPageStateChange).toHaveBeenCalledWith(expect.objectContaining({
      url: "https://news.web.nhk/item1",
      title: "news.web.nhk",
      loading: true,
    }));

    // User can click stop button (Scheme B) to cancel:
    fireEvent.click(stopBtn);
    expect(api.stopBrowser).toHaveBeenCalledWith("session-b", "browser-2");
    expect(screen.queryByRole("progressbar", { name: "网页加载进度" })).not.toBeInTheDocument();
    expect(screen.queryByText("正在连接并加载网页…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
    expect(onPageStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      loading: false,
    }));

    unmount();
  });
});
