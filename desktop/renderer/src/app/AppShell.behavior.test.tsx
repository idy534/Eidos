import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApprovalMode, Item, Run, RuntimeNotification, SessionSnapshot } from "../contracts.js";
import { deriveRuntimePresentation } from "../session-state.js";
import { AppShell } from "./AppShell.js";
import { isQueuedRunListRequest } from "../../../shared/queued-runs.js";

const fixture = vi.hoisted(() => ({
  snapshot: undefined as SessionSnapshot | undefined,
  draft: undefined as SessionSnapshot | undefined,
  forkSession: vi.fn(),
  startDraft: vi.fn(),
  reviseRun: vi.fn(),
  submitInput: vi.fn(),
  setInput: vi.fn(),
  setInputForSession: vi.fn(),
  input: "",
  selectedModelId: undefined as string | undefined,
}));
vi.mock("./useSessionController.js", () => ({
  useSessionController: () => [{
    snapshot: fixture.snapshot, draft: fixture.draft, projects: [], sessions: [], pending: {},
  }, { loadProjects: vi.fn(), loadSessions: vi.fn(), forkSession: fixture.forkSession, startDraft: fixture.startDraft,
    handleRunNotification: vi.fn(), setSnapshot: vi.fn(), refreshCompletedSession: vi.fn(), handleTitleNotification: vi.fn() }],
}));
vi.mock("./useRunController.js", () => ({
  useRunController: () => [{ composerMode: "idle", input: fixture.input, references: [], draftReady: false },
    { reviseRun: fixture.reviseRun, submitInput: fixture.submitInput, setInput: fixture.setInput,
      setInputForSession: fixture.setInputForSession }],
}));
vi.mock("./useApprovalController.js", () => ({
  useApprovalController: () => [{
    approvals: [], respondingApprovalIds: new Set(), respondingKindByApprovalId: {},
    errorsByApprovalId: {}, expiredApprovalIds: new Set(),
  }, { loadPending: vi.fn() }],
}));
vi.mock("./useModelController.js", () => ({
  useModelController: () => [{ selectedModelId: fixture.selectedModelId }, { load: vi.fn() }],
}));
vi.mock("./useExtensionController.js", () => ({
  useExtensionController: () => [{ plugins: [], skills: [], pendingSkillIds: new Set(), mcpServers: [], pendingAction: {} }, { load: vi.fn() }],
}));
vi.mock("./useGitReviewController.js", () => ({ useGitReviewController: () => [{}, { handleNotification: vi.fn() }] }));
vi.mock("./useResponseActionController.js", () => ({
  useResponseActionController: () => [{}, { load: vi.fn() }],
}));
vi.mock("./useContextUsageController.js", () => ({ useContextUsageController: () => [{}, { handleNotification: vi.fn() }] }));
vi.mock("./usePlanningState.js", () => ({ usePlanningState: () => ({}) }));
vi.mock("../components/AgentPanel.js", () => ({ useAgentState: () => ({}) }));
vi.mock("../components/SessionSidebar.js", () => ({
  SessionSidebar: ({ onOpenSettings }: { onOpenSettings: () => void }) => <button onClick={onOpenSettings}>打开设置</button>,
}));
vi.mock("../components/Composer.js", async () => {
  const { ApprovalModeSelector } = await import("../components/ApprovalModeSelector.js");
  return { Composer: ({ approvalMode, onApprovalModeChange, onExecutionModeChange, workMode, onWorkModeChange, onSubmit }: {
    workMode: string; onWorkModeChange: (mode: "goal" | "plan" | "execute") => void; onSubmit: () => void;
    approvalMode: ApprovalMode; onApprovalModeChange: (mode: ApprovalMode) => void;
    onExecutionModeChange?: (mode: "local" | "worktree") => void;
  }) => <>
    <ApprovalModeSelector mode={approvalMode} onChange={onApprovalModeChange} />
    <span data-testid="composer-work-mode">{workMode}</span>
    <button onClick={() => onWorkModeChange("goal")}>选择目标模式</button>
    <button onClick={() => onWorkModeChange("plan")}>选择 Plan 模式</button>
    <button onClick={onSubmit}>发送输入</button>
    {onExecutionModeChange && <button onClick={() => onExecutionModeChange("worktree")}>切换到工作树</button>}
  </> };
});
vi.mock("../components/ComposerSlot.js", () => ({
  ComposerSlot: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("../components/BrowserPanel.js", () => ({ BrowserPanel: () => <p>网页面板</p> }));
vi.mock("../components/InputContext.js", () => ({
  InputContextProvider: ({ children }: { children: React.ReactNode }) => children,
  InputDropZone: ({ children }: { children: React.ReactNode }) => children,
}));
// Keep the real links, result cards, ArtifactProvider, Dock and preview panels under test.
vi.mock("../components/ExecutionFeed.js", async () => {
  const { MarkdownContent } = await import("../components/MarkdownContent.js");
  const { TurnResults } = await import("../components/TurnResults.js");
  return { ExecutionFeed: ({ runs, items, liveThinking, onFork, onRegenerate, onEditResend }: {
    runs: Run[]; items: Item[]; liveThinking?: Record<string, { title: string }>; onFork?: (item: Item) => void;
    onRegenerate?: (run: Run) => void;
    onEditResend?: (run: Run, input: string) => void;
  }) => <>
    {Object.entries(liveThinking ?? {}).map(([runId, value]) => <p key={runId} data-testid="live-thinking">{`${runId}:${value.title}`}</p>)}
    {runs.map((run) => <div key={run.id}>
      <button onClick={() => onRegenerate?.(run)}>重新回答</button>
      <button onClick={() => onEditResend?.(run, "edited question")}>编辑并重新发送</button>
    </div>)}
    {items.filter((item) => item.kind === "assistant_message").map((item) => <button key={item.id} onClick={() => onFork?.(item)}>从这里分叉</button>)}
    <MarkdownContent content={[
    "[第一个文件](/workspace/one.txt)",
    "[第二个文件](/workspace/two.txt)",
    "[再次定位](/workspace/one.txt:2)",
    "[第一个网页](https://example.com/one)",
    "[第二个网页](https://example.com/two)",
  ].join("\n\n")} />{runs.map((run) => <TurnResults key={run.id} run={run} items={items} />)}</> };
});

describe("AppShell text change review", () => {
  it.each([
    ["non-Git", "file"], ["non-Git", "review"],
    ["Git", "file"], ["Git", "review"],
    ["projectless", "file"], ["projectless", "review"],
  ] as const)("opens persisted changes in a %s session through the %s button", async (sessionKind, target) => {
    const snapshot = fixture.snapshot!;
    if (sessionKind === "projectless") {
      delete snapshot.session.project;
      snapshot.session.projectless = true;
    } else {
      snapshot.session.project!.gitAvailable = sessionKind === "Git";
    }
    snapshot.runs = ["older-run", "current-run"].map((id, index) => ({
      id, sessionId: snapshot.session.id, status: "succeeded", modelId: "test-model",
      modelStepCount: 1, createdAt: index + 1, updatedAt: index + 1,
    }));
    snapshot.items = snapshot.runs.map((run, index): Item => {
      const path = index === 0 ? "older.js" : "work/current.js";
      const id = `item-${run.id}`;
      return {
        id, sessionId: snapshot.session.id, runId: run.id, ordinal: index + 1,
        kind: "file_change", status: "completed", createdAt: index + 1,
        toolCall: {
          id: `tool-${id}`, itemId: id, modelStepIndex: 1, batchOrder: 0,
          providerCallId: `provider-${id}`, toolName: "apply_patch", status: "completed",
          startedAt: index + 1,
          // Runtime persists ordinary unified patches without a Git file header.
          changeDiff: `--- a/${path}\n+++ b/${path}\n@@ -1 +1 @@\n-old\n+${run.id}\n`,
          resultJson: JSON.stringify({ outcome: "success", data: { path, changes: [{ kind: "update", path }] } }),
        },
      };
    });
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "打开工作区工具" }));
    expect(screen.queryByRole("button", { name: "文本审查", exact: true })).not.toBeInTheDocument();
    if (sessionKind !== "Git") {
      expect(screen.queryByRole("button", { name: "审查", exact: true })).not.toBeInTheDocument();
    }
    const cardButton = screen.getByRole("button", { name: "已编辑 current.js" });
    const card = cardButton.closest("article")!;
    fireEvent.click(target === "file" ? cardButton : within(card).getByRole("button", { name: "审核", exact: true }));

    const tools = await screen.findByRole("tablist", { name: "工作区窗口" });
    expect(within(tools).getAllByRole("tab", { name: "文本审查" })).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "最近一轮" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "work/current.js" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByRole("button", { name: "older.js" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("tabpanel")).getByText("current-run")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "整个任务" }));
    expect(screen.getByRole("button", { name: "older.js" })).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "审核", exact: true }));
    expect(within(tools).getAllByRole("tab", { name: "文本审查" })).toHaveLength(1);
    expect(screen.getByRole("tab", { name: "最近一轮" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("button", { name: "older.js" })).not.toBeInTheDocument();
    expect(window.eidosRuntime.readWorkspaceFilePreview).not.toHaveBeenCalled();
  });
});

beforeEach(() => {
  fixture.draft = undefined;
  fixture.input = ""; fixture.selectedModelId = undefined;
  fixture.submitInput.mockReset().mockResolvedValue(true); fixture.setInput.mockReset();
  fixture.forkSession.mockReset().mockImplementation(async () => fixture.snapshot);
  fixture.startDraft.mockReset();
  fixture.reviseRun.mockReset().mockResolvedValue(true);
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
    goal: vi.fn(async () => ({})),
    onNotification: vi.fn(() => () => undefined),
    readBrowserAgentStatus: vi.fn(async () => ({
      chromeInstalled: true, running: false, pluginInstalled: false, pluginEnabled: false, resource: "unavailable", granted: false,
    })),
    revokeBrowserAgentGrant: vi.fn(async () => ({ revoked: true })),
    listSidebarPins: vi.fn(async () => ({ items: [], throughEventId: 0 })),
    listWorkspaceDirectory: vi.fn(async () => ({ path: ".", entries: [], truncated: false })),
    readWorkspaceFilePreview: vi.fn(async (_sessionId: string, path: string) => ({
      path, kind: "text", sizeBytes: 30, truncated: false,
      content: `${path} first line\n${path} second line`,
    })),
  } as unknown as typeof window.eidosRuntime;
});

function shell() {
  const status = {
    state: "ready", protocolVersion: 1, runtimeVersion: "test",
    runShell: true, modelConfigured: false, storageHealth: { state: "ready" },
  } as const;
  return <AppShell runtime={{
    status, presentation: deriveRuntimePresentation(status), isStorageReady: true,
    restarting: false, restartRuntime: vi.fn(),
  }} />;
}

function renderShell() { return render(shell()); }

describe("AppShell memory settings Session identity", () => {
  it.each(["empty", "projectless draft", "project draft", "persisted"] as const)(
    "opens memory independently of Session identity for %s",
    async (kind) => {
      const persisted = fixture.snapshot!;
      if (kind !== "persisted") {
        fixture.snapshot = undefined;
        if (kind !== "empty") {
          fixture.draft = { ...persisted, session: { ...persisted.session, id: "draft-unsaved" } };
          if (kind === "projectless draft") {
            delete fixture.draft.session.project;
            fixture.draft.session.projectless = true;
          }
        }
      }
      localStorage.removeItem("eidos.memory.location.v1");
      const projectId = null;
      const scope = "global";
      const memory = vi.fn(async (method: string, request: Record<string, unknown>) => {
        if (request.sessionId != null) {
          throw new Error("EIDOS_RUNTIME_ERROR:MEMORY_SESSION_NOT_FOUND");
        }
        if (method === "memory/read") return {
          path: "MEMORY.md", scopeId: "global", revision: 0, currentRevision: 0,
          revisions: [], document: null, content: "# Memory", offset: 0, nextOffset: null,
        };
        return {
          scopes: [{ id: "global", kind: "global", mode: "auto", privacyEpoch: 0, generation: 0 }],
          documents: [],
        };
      });
      window.eidosRuntime.memory = memory as typeof window.eidosRuntime.memory;
      window.eidosRuntime.exportMemoryNotes = vi.fn(async () => {});
      window.eidosRuntime.listProjects = vi.fn(async () => ({ items: [] }));
      window.eidosRuntime.listModelPresets = vi.fn(async () => ({ providers: [] }));
      renderShell();
      fireEvent.click(screen.getByRole("button", { name: "打开设置" }));
      fireEvent.click(screen.getByRole("tab", { name: "记忆" }));
      await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/read", expect.objectContaining({
        projectId, scope, path: "MEMORY.md",
      })));
      expect(screen.queryByText("记忆无法读取，请重试。")).not.toBeInTheDocument();
      fireEvent.change(screen.getByRole("combobox", { name: "模式" }), { target: { value: "read" } });
      await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/policy", { projectId, scope, mode: "read" }));
      await waitFor(() => expect(screen.getByRole("button", { name: "导出" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "导出" }));
      expect(window.eidosRuntime.exportMemoryNotes).toHaveBeenCalledWith({ scope, projectId });
    },
  );
});

describe("AppShell memory tidying", () => {
  it("opens a projectless draft with an editable tidy request", async () => {
    localStorage.removeItem("eidos.memory.location.v1");
    window.eidosRuntime.memory = vi.fn(async (method: string) => method === "memory/read"
      ? { path: "MEMORY.md", scopeId: "global", revision: 0, currentRevision: 0, revisions: [], document: null,
          content: "# Memory", offset: 0, nextOffset: null }
      : { scopes: [{ id: "global", kind: "global", mode: "auto", privacyEpoch: 0, generation: 0 }], documents: [] },
    ) as typeof window.eidosRuntime.memory;
    window.eidosRuntime.exportMemoryNotes = vi.fn(async () => {});
    window.eidosRuntime.listProjects = vi.fn(async () => ({ items: [] }));
    window.eidosRuntime.listModelPresets = vi.fn(async () => ({ providers: [] }));
    fixture.startDraft.mockReturnValueOnce("draft-tidy");
    fixture.setInputForSession.mockReset();
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "打开设置" }));
    fireEvent.click(screen.getByRole("tab", { name: "记忆" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "整理" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "整理" }));
    await waitFor(() => expect(screen.queryByRole("tab", { name: "记忆" })).not.toBeInTheDocument());
    expect(fixture.startDraft).toHaveBeenCalledExactlyOnceWith(null);
    expect(fixture.setInputForSession).toHaveBeenCalledExactlyOnceWith(
      "draft-tidy", expect.stringContaining("请整理全局记忆"),
    );
    expect(fixture.setInputForSession.mock.calls[0]![1]).toContain("order 设为 oldest");
  });
});

describe("AppShell revision approval modes", () => {
  const cases = (["manual", "auto_review", "full_access"] as const).flatMap((mode) => (
    (["重新回答", "编辑并重新发送"] as const).flatMap((action) => (
      [false, true].map((changed) => ({ mode, action, changed }))
    ))
  ));
  const names = { manual: "请求审批", auto_review: "替我审批", full_access: "完全访问" };

  it.each(cases)("$action uses $mode with changed=$changed", async ({ mode, action, changed }) => {
    const snapshot = fixture.snapshot!;
    const sourceMode = changed ? (mode === "manual" ? "full_access" : "manual") : mode;
    snapshot.runs = [{
      id: "source", sessionId: snapshot.session.id, status: "succeeded", modelId: "test-model",
      approvalMode: sourceMode, modelStepCount: 1, createdAt: 1, updatedAt: 1,
    }];
    renderShell();

    if (changed) {
      fireEvent.click(screen.getByRole("button", { name: `审批模式：${names[sourceMode]}` }));
      fireEvent.click(screen.getByRole("radio", { name: new RegExp(names[mode]) }));
      if (mode === "full_access") {
        expect(screen.getByRole("alertdialog")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "开启完全访问" }));
      }
    }

    expect(screen.getByRole("button", { name: `审批模式：${names[mode]}` })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: action, exact: true }));
    await waitFor(() => expect(fixture.reviseRun).toHaveBeenCalledWith(expect.objectContaining({
      sourceRunId: "source", approvalMode: mode,
      ...(action === "编辑并重新发送" ? { userInput: "edited question" } : {}),
    })));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(snapshot.runs[0]?.approvalMode).toBe(sourceMode);
  });
});

describe("AppShell queued messages in draft Sessions", () => {
  it.each(["projectless", "project"] as const)("reads the %s queue only after the draft becomes a persisted Session", async (kind) => {
    const persisted = fixture.snapshot!;
    persisted.session.id = "00000000-0000-0000-0000-000000000001";
    const draft = { ...persisted, session: { ...persisted.session, id: "draft-00000000-0000-0000-0000-000000000002" } };
    if (kind === "projectless") {
      delete draft.session.project;
      draft.session.projectless = true;
    }
    fixture.snapshot = undefined;
    fixture.draft = draft;
    const listQueuedRuns = vi.fn(async (request: Parameters<typeof window.eidosRuntime.listQueuedRuns>[0]) => {
      if (!isQueuedRunListRequest(request)) throw new Error("排队消息参数无效。");
      return { items: [{ sessionId: persisted.session.id, runId: "queued-follow-up", userInput: "下一轮消息", references: [] }] };
    });
    window.eidosRuntime.listQueuedRuns = listQueuedRuns;
    const view = renderShell();
    // Flush mount effects and rejected IPC promises before inspecting the draft UI.
    await act(async () => {});
    expect(listQueuedRuns).not.toHaveBeenCalled();
    expect(screen.queryByRole("list", { name: "待发送消息" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // First send materializes a real Session; its queue must then become available.
    fixture.snapshot = persisted;
    fixture.draft = undefined;
    view.rerender(shell());
    await screen.findByRole("list", { name: "待发送消息" });
    expect(listQueuedRuns).toHaveBeenCalledWith({ sessionId: persisted.session.id });
    expect(listQueuedRuns).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    // Starting another draft must unmount the previous Session's queue.
    fixture.snapshot = undefined;
    fixture.draft = draft;
    view.rerender(shell());
    await act(async () => {});
    expect(listQueuedRuns).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("list", { name: "待发送消息" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("AppShell conversation fork targets", () => {
  function selectedAnswer() {
    const snapshot = fixture.snapshot!;
    snapshot.items = [{ id: "selected", runId: "ended", sessionId: snapshot.session.id,
      kind: "assistant_message", ordinal: 2, status: "completed", content: "完成", createdAt: 1 }];
    return snapshot;
  }

  it("forks a non-Git conversation into its current directory", async () => {
    const snapshot = selectedAnswer();
    fixture.forkSession.mockImplementationOnce(async () => ({
      ...snapshot,
      session: {
        ...snapshot.session,
        id: "session-forked",
        forkedFrom: {
          sessionId: snapshot.session.id,
          runId: "ended",
          itemId: "selected",
          itemCount: 5,
          createdAt: 1,
        },
      },
    }));
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "从这里分叉" }));
    await waitFor(() => expect(fixture.forkSession).toHaveBeenCalledWith(snapshot.session.id, "selected", { kind: "same_workspace" }));
    expect(screen.queryByRole("alertdialog", { name: "从这里分叉" })).not.toBeInTheDocument();
    expect(await screen.findByText("本会话从历史回答分叉，已继承 5 条记录。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开来源会话" })).not.toBeInTheDocument();
  });

  it("lets a Git conversation choose an independent Worktree", async () => {
    const snapshot = selectedAnswer();
    snapshot.session.project!.gitAvailable = false;
    snapshot.session.roots = [{ id: "root", path: "/workspace", repositoryRoot: "/workspace" }];
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "从这里分叉" }));
    const dialog = await screen.findByRole("alertdialog", { name: "从这里分叉" });
    const target = within(dialog).getByRole("radio", { name: "在独立工作树继续。" });
    fireEvent.click(target);
    fireEvent.click(within(dialog).getByRole("button", { name: "创建分叉" }));
    await waitFor(() => expect(fixture.forkSession).toHaveBeenCalledWith(snapshot.session.id, "selected", { kind: "new_worktree", state: "current_workspace" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: "从这里分叉" })).not.toBeInTheDocument());
  });

  it("uses the Session's frozen Git root after the Project changes", () => {
    const snapshot = fixture.snapshot!;
    snapshot.session.project!.gitAvailable = false;
    snapshot.session.roots = [{
      id: "root",
      path: "/workspace",
      repositoryRoot: "/workspace",
    }];
    renderShell();
    expect(screen.getByRole("button", { name: "切换到工作树" })).toBeInTheDocument();
  });

  it("does not offer Worktree mode when only the current Project has Git", () => {
    const snapshot = fixture.snapshot!;
    snapshot.session.project!.gitAvailable = true;
    snapshot.session.roots = [{ id: "root", path: "/workspace" }];
    renderShell();
    expect(screen.queryByRole("button", { name: "切换到工作树" })).not.toBeInTheDocument();
  });

  it("requires an independent Worktree for managed conversations", async () => {
    const snapshot = selectedAnswer();
    snapshot.session.project!.gitAvailable = true;
    snapshot.session.executionMode = "worktree";
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "从这里分叉" }));
    const dialog = await screen.findByRole("alertdialog", { name: "从这里分叉" });
    expect(within(dialog).getAllByRole("radio")).toHaveLength(1);
    expect(within(dialog).getByRole("radio")).toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(fixture.forkSession).not.toHaveBeenCalled();
  });
});

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

describe("AppShell startup draft", () => {
  it("starts a projectless draft without requiring a new-session click", async () => {
    fixture.snapshot = undefined;
    fixture.draft = undefined;
    renderShell();
    await act(async () => {});
    expect(fixture.startDraft).toHaveBeenCalledExactlyOnceWith(null);
    expect(screen.queryByRole("button", { name: "新建会话" })).not.toBeInTheDocument();
  });
});

describe("AppShell live thinking", () => {
  it("passes a streamed summary to the Feed and drops it, with late ones, when the Run ends", () => {
    let notify: ((notification: RuntimeNotification) => void) | undefined;
    window.eidosRuntime.onNotification = vi.fn((callback: (notification: RuntimeNotification) => void) => {
      notify = callback;
      return () => undefined;
    }) as typeof window.eidosRuntime.onNotification;
    const snapshot = fixture.snapshot!;
    const run: Run = {
      id: "live-run", sessionId: snapshot.session.id, status: "running", modelId: "test-model",
      modelStepCount: 1, createdAt: 1, updatedAt: 1, startedAt: 1,
    };
    snapshot.runs = [run];
    renderShell();
    expect(screen.queryByTestId("live-thinking")).not.toBeInTheDocument();

    const thinking = (summaryId: string, title: string): RuntimeNotification => ({
      method: "run/thinking",
      params: { sessionId: snapshot.session.id, runId: run.id, summaryId, title },
    });
    act(() => notify!(thinking("rs:0", "确认技能目录")));
    expect(screen.getByTestId("live-thinking")).toHaveTextContent("live-run:确认技能目录");

    act(() => notify!({ method: "run/updated", params: { sessionId: snapshot.session.id, run: { ...run, status: "failed", completedAt: 2 } } }));
    expect(screen.queryByTestId("live-thinking")).not.toBeInTheDocument();
    act(() => notify!(thinking("rs:1", "迟到的摘要")));
    expect(screen.queryByTestId("live-thinking")).not.toBeInTheDocument();
  });
});


describe("AppShell Composer goal mode", () => {
  it("selects the mode without opening a setup form and starts with Composer permissions", async () => {
    fixture.selectedModelId = "test-model";
    fixture.input = "修复登录模块并确认测试通过。";
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "选择目标模式" }));
    await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("goal"));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(fixture.submitInput).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "发送输入" }));
    await waitFor(() => expect(fixture.submitInput).toHaveBeenCalledWith(expect.objectContaining({
      selectedModelId: "test-model", approvalMode: "manual", planning: { workMode: "execute", goalMode: true },
    })));
    expect(window.eidosRuntime.goal).toHaveBeenCalledWith({ action: "get", sessionId: "session-a" });
  });

  it("keeps a draft unmaterialized when only choosing goal mode", async () => {
    fixture.draft = { ...fixture.snapshot!, session: { ...fixture.snapshot!.session, id: "draft" } };
    fixture.snapshot = undefined;
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "选择目标模式" }));
    await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("goal"));
    expect(window.eidosRuntime.goal).not.toHaveBeenCalled();
    expect(fixture.submitInput).not.toHaveBeenCalled();
  });

  it("treats bare /goal as a mode selection and preserves direct input submission", async () => {
    fixture.selectedModelId = "test-model";
    fixture.input = "/goal";
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "发送输入" }));
    await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("goal"));
    expect(fixture.setInput).toHaveBeenCalledWith("");
    expect(fixture.submitInput).not.toHaveBeenCalled();
  });

  it("pauses the persistent goal before switching to Plan mode", async () => {
    const saved = { id: "goal", sessionId: "session-a", objective: "完整目标。", status: "active", revision: 1,
      objectiveRevision: 1, activation: 1, model: { modelId: "test-model" }, approvalMode: "manual", createdAt: 1, updatedAt: 1 };
    window.eidosRuntime.goal = vi.fn(async (request) => ({ goal: { ...saved, status: request.action === "pause" ? "paused" : "active" } })) as typeof window.eidosRuntime.goal;
    renderShell();
    await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("goal"));
    fireEvent.click(screen.getByRole("button", { name: "选择 Plan 模式" }));
    await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("plan"));
    expect(window.eidosRuntime.goal).toHaveBeenCalledWith(expect.objectContaining({ action: "pause", expectedRevision: 1 }));
  });
});

it("exits the armed goal mode when Runtime pauses the active goal", async () => {
  let status: "active" | "paused" = "active";
  const saved = { id: "goal", sessionId: "session-a", objective: "保留完整目标。", revision: 1, objectiveRevision: 1, activation: 1,
    model: { modelId: "test-model" }, approvalMode: "manual" as const, createdAt: 1, updatedAt: 1 };
  window.eidosRuntime.goal = vi.fn(async () => ({ goal: { ...saved, status } }));
  renderShell();
  await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("goal"));
  fireEvent.click(screen.getByRole("button", { name: "选择目标模式" }));
  status = "paused";
  const calls = vi.mocked(window.eidosRuntime.onNotification).mock.calls;
  act(() => { for (const [callback] of calls) callback({ method: "goal/updated", params: { sessionId: "session-a" } }); });
  await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("execute"));
  expect(await screen.findByRole("button", { name: "继续目标" })).toBeInTheDocument();
});

it("leaves goal mode after completion so a follow-up message does not start a new goal", async () => {
  fixture.selectedModelId = "test-model";
  fixture.input = "谢谢，顺便把按钮颜色改深一点。";
  let status: "active" | "complete" = "active";
  const saved = { id: "goal", sessionId: "session-a", objective: "完整目标。", revision: 3, objectiveRevision: 1, activation: 1,
    model: { modelId: "test-model" }, approvalMode: "manual" as const, createdAt: 1, updatedAt: 1 };
  window.eidosRuntime.goal = vi.fn(async () => ({ goal: { ...saved, status } }));
  renderShell();
  await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("goal"));
  fireEvent.click(screen.getByRole("button", { name: "选择目标模式" }));
  status = "complete";
  const calls = vi.mocked(window.eidosRuntime.onNotification).mock.calls;
  act(() => { for (const [callback] of calls) callback({ method: "goal/updated", params: { sessionId: "session-a" } }); });
  await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("execute"));
  fireEvent.click(screen.getByRole("button", { name: "发送输入" }));
  await waitFor(() => expect(fixture.submitInput).toHaveBeenCalledWith(expect.objectContaining({ planning: { workMode: "execute" } })));
});

it("sends active-goal feedback with its expected version and keeps the original target", async () => {
  fixture.selectedModelId = "test-model";
  fixture.input = "还需要核对错误分支。";
  const saved = { id: "goal", sessionId: "session-a", objective: "完整目标。", status: "active" as const, revision: 3,
    objectiveRevision: 1, activation: 1, model: { modelId: "test-model" }, approvalMode: "manual" as const, createdAt: 1, updatedAt: 1 };
  window.eidosRuntime.goal = vi.fn(async () => ({ goal: saved }));
  renderShell();
  await waitFor(() => expect(screen.getByTestId("composer-work-mode")).toHaveTextContent("goal"));
  fireEvent.click(screen.getByRole("button", { name: "发送输入" }));
  await waitFor(() => expect(fixture.submitInput).toHaveBeenCalledWith(expect.objectContaining({ planning: { workMode: "execute", goalRevision: 3 } })));
  expect(window.eidosRuntime.goal).toHaveBeenCalledTimes(1);
});
