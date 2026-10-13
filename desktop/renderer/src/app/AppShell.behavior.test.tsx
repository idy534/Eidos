import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionSnapshot } from "../contracts.js";
import { deriveRuntimePresentation } from "../session-state.js";
import { AppShell } from "./AppShell.js";

const fixture = vi.hoisted(() => ({ snapshot: undefined as SessionSnapshot | undefined }));
vi.mock("./useSessionController.js", () => ({
  useSessionController: () => [{
    snapshot: fixture.snapshot, projects: [], sessions: [], pending: {},
  }, { loadProjects: vi.fn(), loadSessions: vi.fn() }],
}));
vi.mock("./useRunController.js", () => ({
  useRunController: () => [{ composerMode: "idle", input: "", references: [], draftReady: false }, {}],
}));
vi.mock("./useApprovalController.js", () => ({
  useApprovalController: () => [{
    approvals: [], respondingApprovalIds: new Set(), respondingKindByApprovalId: {},
    errorsByApprovalId: {}, expiredApprovalIds: new Set(),
  }, { loadPending: vi.fn() }],
}));
vi.mock("./useModelController.js", () => ({
  useModelController: () => [{}, { load: vi.fn() }],
}));
vi.mock("./useExtensionController.js", () => ({ useExtensionController: () => [{}, {}] }));
vi.mock("./useGitReviewController.js", () => ({ useGitReviewController: () => [{}, {}] }));
vi.mock("./useResponseActionController.js", () => ({
  useResponseActionController: () => [{}, { load: vi.fn() }],
}));
vi.mock("./useContextUsageController.js", () => ({ useContextUsageController: () => [{}, {}] }));
vi.mock("./usePlanningState.js", () => ({ usePlanningState: () => ({}) }));
vi.mock("../components/AgentPanel.js", () => ({ useAgentState: () => ({}) }));
vi.mock("../components/SessionSidebar.js", () => ({ SessionSidebar: () => null }));
vi.mock("../components/Composer.js", () => ({ Composer: () => null }));
vi.mock("../components/ComposerSlot.js", () => ({ ComposerSlot: () => null }));
vi.mock("../components/BrowserPanel.js", () => ({ BrowserPanel: () => <p>网页面板</p> }));
vi.mock("../components/InputContext.js", () => ({
  InputContextProvider: ({ children }: { children: React.ReactNode }) => children,
  InputDropZone: ({ children }: { children: React.ReactNode }) => children,
}));
// Keep the real Markdown link, ArtifactProvider, Dock and Explorer path under test.
vi.mock("../components/ExecutionFeed.js", async () => {
  const { MarkdownContent } = await import("../components/MarkdownContent.js");
  return { ExecutionFeed: () => <MarkdownContent content={[
    "[第一个文件](/workspace/one.txt)",
    "[第二个文件](/workspace/two.txt)",
    "[再次定位](/workspace/one.txt:2)",
    "[第一个网页](https://example.com/one)",
    "[第二个网页](https://example.com/two)",
  ].join("\n\n")} /> };
});

beforeEach(() => {
  fixture.snapshot = {
    session: {
      id: "session-a", workspaceRoot: "/workspace", executionMode: "local",
      taskStatus: "in_progress", createdAt: 0, updatedAt: 0,
      project: { id: "project-a", name: "Project", workspaceRoot: "/workspace", gitAvailable: false },
    },
    runs: [], items: [], stepResolutions: [],
  };
  Object.defineProperty(window, "scrollTo", { configurable: true, value: vi.fn() });
  window.eidosRuntime = {
    onNotification: vi.fn(() => () => undefined),
    listWorkspaceDirectory: vi.fn(async () => ({ path: ".", entries: [], truncated: false })),
    readWorkspaceFilePreview: vi.fn(async (_sessionId: string, path: string) => ({
      path, kind: "text", sizeBytes: 30, truncated: false,
      content: `${path} first line\n${path} second line`,
    })),
  } as unknown as typeof window.eidosRuntime;
});

function renderShell() {
  const status = {
    state: "ready", protocolVersion: 1, runtimeVersion: "test",
    runShell: true, modelConfigured: false, storageHealth: { state: "ready" },
  } as const;
  return render(<AppShell runtime={{
    status, presentation: deriveRuntimePresentation(status), isStorageReady: true,
    restarting: false, restartRuntime: vi.fn(),
  }} />);
}

function expectOneFilesPanel() {
  const tools = screen.getByRole("tablist", { name: "工作区窗口" });
  expect(within(tools).getAllByRole("tab", { name: "文件" })).toHaveLength(1);
  expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  return screen.getByRole("tablist", { name: "打开的文件" });
}

describe("AppShell workspace file links", () => {
  it("opens successive Session links as file tabs in one Files panel", async () => {
    renderShell();
    fireEvent.click(screen.getByRole("link", { name: "第一个文件" }));
    await screen.findByText("one.txt first line");
    fireEvent.click(screen.getByRole("link", { name: "第二个文件" }));
    await screen.findByText("two.txt first line");

    const files = expectOneFilesPanel();
    expect(within(files).getAllByRole("tab")).toHaveLength(2);
    expect(within(files).getByRole("tab", { name: "two.txt" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(within(files).getByRole("tab", { name: "one.txt" }));
    await screen.findByText("one.txt first line");
    fireEvent.click(screen.getByRole("link", { name: "再次定位" }));
    await waitFor(() => expect(window.getSelection()?.toString()).toBe("one.txt second line"));
    expect(within(expectOneFilesPanel()).getAllByRole("tab")).toHaveLength(2);
    expect(window.eidosRuntime.readWorkspaceFilePreview).toHaveBeenCalledTimes(2);
  });

  it("reuses a Files panel opened from the tool menu and reopens it after closing", async () => {
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "打开工作区工具" }));
    fireEvent.click(screen.getByRole("button", { name: "文件", exact: true }));
    fireEvent.click(screen.getByRole("link", { name: "第一个文件" }));
    await screen.findByText("one.txt first line");
    expectOneFilesPanel();

    fireEvent.click(screen.getByRole("button", { name: "关闭文件", exact: true }));
    fireEvent.click(screen.getByRole("link", { name: "第二个文件" }));
    await screen.findByText("two.txt first line");
    expect(within(expectOneFilesPanel()).getAllByRole("tab")).toHaveLength(1);
  });

  it("deduplicates Files openings queued before the next render", async () => {
    renderShell();
    act(() => {
      fireEvent.click(screen.getByRole("link", { name: "第一个文件" }));
      fireEvent.click(screen.getByRole("link", { name: "第二个文件" }));
    });
    await screen.findByText("two.txt first line");
    expect(within(expectOneFilesPanel()).getAllByRole("tab")).toHaveLength(1);
  });

  it("keeps separate Browser tabs while file links reuse Files", async () => {
    renderShell();
    fireEvent.click(screen.getByRole("link", { name: "第一个网页" }));
    fireEvent.click(screen.getByRole("link", { name: "第二个网页" }));
    fireEvent.click(screen.getByRole("link", { name: "第一个文件" }));
    await screen.findByText("one.txt first line");
    fireEvent.click(screen.getByRole("link", { name: "第二个文件" }));
    await screen.findByText("two.txt first line");
    const tools = screen.getByRole("tablist", { name: "工作区窗口" });
    expect(within(tools).getAllByRole("tab")).toHaveLength(3);
    expect(within(tools).getAllByRole("tab", { name: "文件" })).toHaveLength(1);
    expect(within(tools).getByRole("tab", { name: "新标签页", exact: true })).toBeInTheDocument();
    expect(within(tools).getByRole("tab", { name: "新标签页 2" })).toBeInTheDocument();
  });
});
