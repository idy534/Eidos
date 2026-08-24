import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { App } from "../App.js";
import type { EidosRuntimeAPI, Project, RuntimeStatus, Session, SessionSnapshot } from "../contracts.js";

const mockReadyStatus: RuntimeStatus = {
  state: "ready",
  protocolVersion: 1,
  runtimeVersion: "0.3.0",
  runShell: true,
  modelConfigured: true,
  storageHealth: { state: "ready" },
};

const mockHealthOnlyStatus: RuntimeStatus = {
  state: "ready",
  protocolVersion: 1,
  runtimeVersion: "0.3.0",
  runShell: true,
  modelConfigured: true,
  storageHealth: { state: "health_only", code: "READ_ONLY_STORAGE" },
};
const startupSession: Session = {
  id: "startup-session",
  title: "Startup session",
  workspaceRoot: "/workspace/startup",
  taskStatus: "new",
  createdAt: 1,
  updatedAt: 1,
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("App & Runtime Lifecycle behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupMockRuntime(overrides: Partial<EidosRuntimeAPI> = {}) {
    let statusListener: ((status: RuntimeStatus) => void) | undefined;
    const unsubSpy = vi.fn();
    const onStatusSpy = vi.fn().mockImplementation((cb) => {
      statusListener = cb;
      return unsubSpy;
    });
    const getStatusSpy = vi.fn().mockResolvedValue(mockReadyStatus);

    const api: Partial<EidosRuntimeAPI> = {
      getStatus: getStatusSpy,
      getHealth: vi.fn().mockResolvedValue({ state: "ready" }),
      onStatus: onStatusSpy,
      onShortcut: vi.fn().mockReturnValue(() => {}),
      onNotification: vi.fn().mockReturnValue(() => {}),
      onApprovalRequest: vi.fn().mockReturnValue(() => {}),
      listProjects: vi.fn().mockResolvedValue({ items: [] }),
      listSessions: vi.fn().mockResolvedValue({ items: [] }),
      deleteProject: vi.fn().mockResolvedValue({ deletedProjectId: "project-1" }),
      listModels: vi.fn().mockResolvedValue({
        defaultModelId: "deepseek-v4-flash",
        models: [{
          id: "deepseek-v4-flash", name: "DeepSeek-V4 Flash", vendor: "DeepSeek",
          provider: "deepseek", url: "https://api.deepseek.com/chat/completions",
          supportsToolCall: true, supportsImages: false, supportsReasoning: true,
          reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
        }],
      }),
      listPendingApprovals: vi.fn().mockResolvedValue([]),
      readExtensions: vi.fn().mockResolvedValue({ plugins: [], skills: [], servers: [], throughEventId: 0 }),
      readExtensionEvents: vi.fn().mockResolvedValue({ items: [], throughEventId: 0 }),
      ...overrides,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    return {
      api,
      getStatusSpy,
      onStatusSpy,
      unsubSpy,
      emitStatus: (s: RuntimeStatus) => {
        if (statusListener) statusListener(s);
      },
    };
  }

  it("getStatus() called once, onStatus() subscribed once, unsubscribed on unmount", async () => {
    const { getStatusSpy, onStatusSpy, unsubSpy } = setupMockRuntime();

    const { unmount } = render(<App />);

    await waitFor(() => {
      expect(getStatusSpy).toHaveBeenCalledTimes(1);
      expect(onStatusSpy).toHaveBeenCalledTimes(1);
    });

    expect(unsubSpy).not.toHaveBeenCalled();

    unmount();
    expect(unsubSpy).toHaveBeenCalledTimes(1);
  });

  it("starting renders RuntimeGate with status role", () => {
    setupMockRuntime({
      getStatus: vi.fn().mockReturnValue(Promise.withResolvers<RuntimeStatus>().promise),
    });

    const { container } = render(<App />);

    const gate = container.querySelector(".runtime-gate");
    expect(gate).toBeInTheDocument();
    expect(gate).toHaveAttribute("role", "status");
    expect(gate).toHaveTextContent("正在启动 Engine");
  });

  it("error state renders alert role in RuntimeGate", async () => {
    setupMockRuntime({
      getStatus: vi.fn().mockResolvedValue({ state: "error", message: "Failed to bind runtime port" }),
    });

    const { container } = render(<App />);

    await waitFor(() => {
      const gate = container.querySelector(".runtime-gate");
      expect(gate).toBeInTheDocument();
      expect(gate).toHaveAttribute("role", "alert");
      expect(gate).toHaveTextContent("启动失败");
    });
  });

  it("ready state renders AppShell workbench", async () => {
    setupMockRuntime();
    const { container } = render(<App />);

    await waitFor(() => {
      const workbench = container.querySelector(".workspace");
      expect(workbench).toBeInTheDocument();
    });
  });

  it("keeps loaded sessions selectable before a session snapshot is selected", async () => {
    setupMockRuntime({
      listSessions: vi.fn().mockResolvedValue({ items: [startupSession] }),
    });

    render(<App />);

    const sessionButton = await screen.findByRole("button", { name: startupSession.title });
    expect(sessionButton).toBeEnabled();
  });

  it("hides Files for a projectless session", async () => {
    const projectlessSession: Session = {
      ...startupSession,
      id: "projectless-session",
      title: "Projectless conversation",
      taskStatus: "in_progress",
      projectless: true,
      workspaceRoot: "/private/projectless-session",
    };
    const projectlessSnapshot: SessionSnapshot = {
      session: projectlessSession,
      runs: [],
      items: [],
      stepResolutions: [],
      throughEventId: 0,
    };
    setupMockRuntime({
      listSessions: vi.fn().mockResolvedValue({ items: [projectlessSession] }),
      readSession: vi.fn().mockResolvedValue(projectlessSnapshot),
      listEvents: vi.fn().mockResolvedValue({ items: [], throughEventId: 0, hasMore: false }),
    });

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: new RegExp(projectlessSession.title) }));
    await screen.findByRole("textbox", { name: "告诉 Eidos 要做什么" });
    expect(screen.queryByRole("button", { name: "Files" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开工作区工具" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("会话上下文")).not.toBeInTheDocument();
  });

  it("opens project tools while keeping the conversation mounted", async () => {
    const switchSessionGitBranch = vi.fn().mockResolvedValue({ branch: "main" });
    const project: Project = {
      id: "project-git",
      name: "worktree-test",
      workspaceRoot: "/workspace/worktree-test",
      gitAvailable: true,
      createdAt: 1,
      updatedAt: 1,
    };
    const session: Session = {
      ...startupSession,
      id: "git-session",
      title: "Review workspace",
      taskStatus: "in_progress",
      workspaceRoot: project.workspaceRoot,
      executionMode: "local",
      project,
    };
    const snapshot: SessionSnapshot = {
      session,
      runs: [],
      items: [],
      stepResolutions: [],
      throughEventId: 0,
    };
    setupMockRuntime({
      listProjects: vi.fn().mockResolvedValue({ items: [project] }),
      listSessions: vi.fn().mockResolvedValue({ items: [session] }),
      readSession: vi.fn().mockResolvedValue(snapshot),
      listEvents: vi.fn().mockResolvedValue({ items: [], throughEventId: 0, hasMore: false }),
      readSessionGitStatus: vi.fn().mockResolvedValue({
        worktreeId: null,
        branch: "feature/review",
        head: "b".repeat(40),
        baseRef: null,
        baseCommit: null,
        dirty: true,
        stagedCount: 0,
        unstagedCount: 1,
        untrackedCount: 0,
        conflictCount: 0,
        stagedFiles: [],
        unstagedFiles: ["README.md"],
        untrackedFiles: [],
        conflictFiles: [],
        observedAt: 1,
      }),
      readSessionGitDiff: vi.fn().mockResolvedValue({
        scope: "baseline",
        compareRef: "origin/main",
        baseCommit: "a".repeat(40),
        head: "b".repeat(40),
        dirty: true,
        changedFiles: ["README.md"],
        unifiedDiff: "diff --git a/README.md b/README.md\n",
        diffHash: "summary-hash",
        truncated: false,
        additions: 1,
        deletions: 0,
        statsIncomplete: false,
        fileStats: [{ path: "README.md", additions: 1, deletions: 0, statsIncomplete: false }],
        observedAt: 1,
      }),
      readProjectGitContext: vi.fn().mockResolvedValue({
        gitAvailable: true,
        currentBranch: "feature/review",
        head: "b".repeat(40),
        branches: ["feature/review", "main"],
        dirty: true,
        changedFileCount: 1,
      }),
      listReviewComments: vi.fn().mockResolvedValue([]),
      switchSessionGitBranch,
      readSessionGitRemoteStatus: vi.fn().mockResolvedValue({
        branch: "feature/review",
        remotes: [{ name: "origin" }],
        upstream: { remote: "origin", branch: "main" },
        ahead: 1,
        behind: 0,
      }),
    });

    const { container } = render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(session.title!) }));

    expect(await screen.findByRole("button", { name: "环境信息" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "打开工作区工具" })).toHaveAttribute("aria-expanded", "false");
    expect(container.querySelector(".workspace-body")).toHaveClass("workspace-body--session-centered");
    expect(screen.queryByRole("button", { name: "对话" })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "告诉 Eidos 要做什么" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "打开工作区工具" }));

    expect(await screen.findByRole("complementary", { name: "工作区工具" })).toBeInTheDocument();
    const dockHeader = container.querySelector(".workspace-dock__header");
    expect(container.querySelector(".workspace-body__actions")).not.toBeInTheDocument();
    expect(dockHeader).toContainElement(screen.getByRole("button", { name: "环境信息" }));
    expect(dockHeader).toContainElement(screen.getByRole("button", { name: "关闭工作区工具" }));
    fireEvent.click(screen.getByRole("button", { name: "添加窗口" }));
    expect(screen.getByRole("menu", { name: "添加窗口" })).toBeVisible();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("tab", { name: "审阅" })).not.toBeInTheDocument();
    expect(screen.getByText("打开工作区").closest("[role=status]")).toHaveTextContent("打开工作区");
    expect(screen.getByRole("textbox", { name: "告诉 Eidos 要做什么" })).toBeInTheDocument();
    expect(container.querySelector(".workspace-main")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "关闭工作区工具" })).toHaveLength(1);
    const dockResizeHandle = screen.getByRole("separator", { name: "调整工作区宽度" });
    fireEvent.keyDown(dockResizeHandle, { key: "ArrowLeft" });
    expect(container.querySelector(".workspace-body")?.getAttribute("style"))
      .toContain("--workspace-dock-width");

    fireEvent.click(screen.getByRole("button", { name: "审阅" }));
    expect(screen.getByRole("tab", { name: "审阅" })).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("button", { name: "展开工作区" }));
    expect(container.querySelector(".workspace-body")).toHaveClass("workspace-body--expanded");
    expect(container.querySelector(".workspace-main-column")).toHaveClass("workspace-main-column--hidden");

    fireEvent.click(screen.getByRole("button", { name: "环境信息" }));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("region", { name: "环境信息预览" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "环境信息" }));
    const reopenedEnvironment = screen.getByRole("region", { name: "环境信息预览" });
    expect(within(reopenedEnvironment).queryByText("›")).not.toBeInTheDocument();
    expect(within(reopenedEnvironment).getByText("本地")).toBeInTheDocument();
    fireEvent.click(within(reopenedEnvironment).getByRole("button", { name: "更改工作环境" }));
    const environmentDialog = await screen.findByRole("dialog", { name: "更改工作环境" });
    fireEvent.click(within(environmentDialog).getByRole("radio", { name: /^本地/ }));
    fireEvent.change(within(environmentDialog).getByRole("combobox", { name: "本地分支" }), {
      target: { value: "main" },
    });
    fireEvent.click(within(environmentDialog).getByRole("button", { name: "切换到 main" }));
    await waitFor(() => expect(switchSessionGitBranch).toHaveBeenCalledWith(
      session.id,
      "main",
      expect.any(String),
    ));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "更改工作环境" })).not.toBeInTheDocument());
    fireEvent.click(within(reopenedEnvironment).getByRole("button", { name: "提交或推送" }));
    expect(await screen.findByRole("dialog", { name: "提交和推送" })).toBeInTheDocument();
  });

  it("health-only state remains in ready application and presents read-only warning", async () => {
    setupMockRuntime({
      getStatus: vi.fn().mockResolvedValue(mockHealthOnlyStatus),
    });

    const { container } = render(<App />);

    await waitFor(() => {
      const workbench = container.querySelector(".workspace");
      expect(workbench).toBeInTheDocument();
      expect(container).toHaveTextContent("存储处于只读健康模式");
    });
  });
});
