import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ArtifactProvider, type ArtifactActions } from "./ArtifactContext.js";
import { MarkdownContent } from "./MarkdownContent.js";

vi.mock("shiki", () => ({
  codeToHtml: vi.fn(async () => '<pre class="shiki"><code>const stable = true;</code></pre>'),
}));

const content = "```javascript\nconst stable = true;\n```\n\n![Preview](/workspace/image.png)\n\n[File](/workspace/file.ts)\n\n[Web](https://example.com)";
const baseActions: ArtifactActions = {
  sessionId: "session-a", executionRoot: "/workspace", openFile: vi.fn(), openBrowser: vi.fn(),
};
const document = (actions: ArtifactActions) => (
  <ArtifactProvider value={actions}><MarkdownContent className="eidos-chat-prose" content={content} /></ArtifactProvider>
);

describe("MarkdownContent context updates", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves loaded code and image nodes when parent callbacks change", async () => {
    const prepare = vi.fn().mockResolvedValue("eidos-preview://image");
    const release = vi.fn().mockResolvedValue(undefined);
    Object.assign(window, { eidosRuntime: { prepareWorkspacePreview: prepare, releaseWorkspacePreview: release } });
    const { container, rerender } = render(document(baseActions));
    const image = await screen.findByRole("img", { name: "Preview" });
    await waitFor(() => expect(container.querySelector(".shiki-code-body")).not.toBeNull());
    const code = screen.getByRole("region", { name: "javascript片段" });
    const highlight = container.querySelector(".shiki-code-body");
    const nextActions = { ...baseActions, openFile: vi.fn(), openBrowser: vi.fn() };

    rerender(document(nextActions));

    expect(screen.getByRole("region", { name: "javascript片段" })).toBe(code);
    expect(container.querySelector(".shiki-code-body")).toBe(highlight);
    expect(screen.getByRole("img", { name: "Preview" })).toBe(image);
    expect(prepare).toHaveBeenCalledTimes(1);
    expect(release).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("link", { name: "File" }));
    fireEvent.click(screen.getByRole("link", { name: "Web" }));
    expect(nextActions.openFile).toHaveBeenCalledWith("file.ts");
    expect(nextActions.openBrowser).toHaveBeenCalledWith("https://example.com");
    expect(baseActions.openFile).not.toHaveBeenCalled();
  });

  it("reloads previews when the displayed Session changes without remounting code", async () => {
    const prepare = vi.fn().mockResolvedValueOnce("eidos-preview://a").mockResolvedValueOnce("eidos-preview://b");
    const release = vi.fn().mockResolvedValue(undefined);
    Object.assign(window, { eidosRuntime: { prepareWorkspacePreview: prepare, releaseWorkspacePreview: release } });
    const { rerender } = render(document(baseActions));
    expect(await screen.findByRole("img", { name: "Preview" })).toHaveAttribute("src", "eidos-preview://a");
    const code = screen.getByRole("region", { name: "javascript片段" });

    rerender(document({ ...baseActions, sessionId: "session-b" }));

    await waitFor(() => expect(screen.getByRole("img", { name: "Preview" })).toHaveAttribute("src", "eidos-preview://b"));
    expect(prepare).toHaveBeenLastCalledWith("session-b", "image.png", undefined, "/workspace", undefined);
    expect(release).toHaveBeenCalledWith("eidos-preview://a");
    expect(screen.getByRole("region", { name: "javascript片段" })).toBe(code);
  });

  it("shows link and file path tooltips on hover", () => {
    vi.useFakeTimers();
    try {
      render(
        <ArtifactProvider value={baseActions}>
          <MarkdownContent className="eidos-chat-prose" content={"[File](/workspace/file.ts)\n\n[Web](https://example.com)\n\n[Relative](relative.txt)"} />
        </ArtifactProvider>,
      );
      const webLink = screen.getByRole("link", { name: "Web" });
      const fileLink = screen.getByRole("link", { name: "File" });
      const fallback = screen.getByText("Relative");

      // Hover web link
      fireEvent.mouseEnter(webLink);
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(200);
      });
      let tooltip = screen.getByRole("tooltip");
      expect(tooltip).toBeInTheDocument();
      expect(tooltip).toHaveTextContent("https://example.com");
      expect(tooltip).toHaveClass("eidos-link-tooltip");

      fireEvent.mouseLeave(webLink);
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

      // Hover file link (prioritizes absolute path when executionRoot is absolute)
      fireEvent.mouseEnter(fileLink);
      act(() => {
        vi.advanceTimersByTime(200);
      });
      tooltip = screen.getByRole("tooltip");
      expect(tooltip).toBeInTheDocument();
      expect(tooltip).toHaveTextContent("/workspace/file.ts");
      expect(tooltip).toHaveClass("eidos-link-tooltip");

      fireEvent.mouseLeave(fileLink);
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

      // Hover fallback link
      fireEvent.mouseEnter(fallback);
      act(() => {
        vi.advanceTimersByTime(200);
      });
      tooltip = screen.getByRole("tooltip");
      expect(tooltip).toBeInTheDocument();
      expect(tooltip).toHaveTextContent("relative.txt");
      expect(tooltip).toHaveClass("eidos-link-tooltip");

      // Hover file link again and click to suppress tooltip
      fireEvent.mouseLeave(fallback);
      fireEvent.mouseEnter(fileLink);
      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(screen.getByRole("tooltip")).toBeInTheDocument();
      fireEvent.click(fileLink);
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("MarkdownContent answer images", () => {
  const renderAnswer = (markdown: string, actions: ArtifactActions = baseActions) => render(
    <ArtifactProvider value={actions}><MarkdownContent className="eidos-chat-prose" content={markdown} /></ArtifactProvider>,
  );
  const installRuntime = (prepare = vi.fn().mockResolvedValue("eidos-preview://image")) => {
    const release = vi.fn().mockResolvedValue(undefined);
    Object.assign(window, { eidosRuntime: { prepareWorkspacePreview: prepare, releaseWorkspacePreview: release } });
    return { prepare, release };
  };

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reads absolute images from another Session root and opens them in that root", async () => {
    const { prepare } = installRuntime();
    const actions: ArtifactActions = {
      ...baseActions,
      openFile: vi.fn(),
      roots: [
        { id: "root-a", path: "/workspace" },
        { id: "root-b", path: "/docs" },
      ] as ArtifactActions["roots"],
    };

    renderAnswer("段落\n\n![架构图](</docs/assets/arch.png>)\n\n后续段落", actions);

    fireEvent.click(await screen.findByRole("button", { name: "打开 架构图" }));
    expect(prepare).toHaveBeenCalledWith("session-a", "assets/arch.png", undefined, "/docs", undefined);
    expect(actions.openFile).toHaveBeenCalledWith("assets/arch.png", { rootId: "root-b" });
  });

  it("keeps primary-root images on the original open path", async () => {
    installRuntime();
    const actions = { ...baseActions, openFile: vi.fn() };

    renderAnswer("![图表](</workspace/out/chart.png>)", actions);

    fireEvent.click(await screen.findByRole("button", { name: "打开 图表" }));
    expect(actions.openFile).toHaveBeenCalledWith("out/chart.png");
  });

  it("does not read images outside every Session root", () => {
    const { prepare } = installRuntime();

    renderAnswer("![外部图](</tmp/chart.png>)");

    expect(screen.getByText("外部图（不在当前工作区，无法显示）")).toHaveAttribute("title", "/tmp/chart.png");
    expect(prepare).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("never fetches remote images and opens them only on request", () => {
    const { prepare } = installRuntime();
    const actions = { ...baseActions, openBrowser: vi.fn() };

    const { container } = renderAnswer("![趋势](https://example.com/trend.png?q=1)", actions);

    expect(container.querySelector("img")).toBeNull();
    expect(prepare).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "在浏览器中打开远程图片 趋势（example.com）" }));
    expect(actions.openBrowser).toHaveBeenCalledWith("https://example.com/trend.png?q=1");
  });

  it("shows a fallback when the image cannot be decoded", async () => {
    installRuntime();

    renderAnswer("![损坏](</workspace/broken.png>)");

    fireEvent.error(await screen.findByRole("img", { name: "损坏" }));
    expect(screen.getByRole("status")).toHaveTextContent("损坏：图片加载失败，请刷新文件。");
  });

  it("holds a preview grant only while the image is near the viewport", async () => {
    const observers: Array<{ callback: IntersectionObserverCallback; disconnect: ReturnType<typeof vi.fn> }> = [];
    vi.stubGlobal("IntersectionObserver", class {
      disconnect = vi.fn();
      constructor(public callback: IntersectionObserverCallback) { observers.push(this); }
      observe() {}
      unobserve() {}
      takeRecords() { return []; }
    });
    const { prepare, release } = installRuntime();
    const notify = (isIntersecting: boolean) => act(() => {
      observers[0]!.callback([{ isIntersecting } as IntersectionObserverEntry], {} as IntersectionObserver);
    });

    renderAnswer("![远处的图](</workspace/far.png>)");
    expect(prepare).not.toHaveBeenCalled();

    notify(true);
    const image = await screen.findByRole("img", { name: "远处的图" });
    vi.spyOn(image, "offsetWidth", "get").mockReturnValue(320);
    vi.spyOn(image, "offsetHeight", "get").mockReturnValue(180);
    fireEvent.load(image);

    notify(false);
    await waitFor(() => expect(release).toHaveBeenCalledWith("eidos-preview://image"));
    const placeholder = screen.getByRole("img", { name: "远处的图" });
    expect(placeholder.tagName).toBe("SPAN");
    expect(placeholder).toHaveStyle({ width: "320px", height: "180px" });

    notify(true);
    await waitFor(() => expect(prepare).toHaveBeenCalledTimes(2));
  });
});
