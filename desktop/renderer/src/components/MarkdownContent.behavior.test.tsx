import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
});
