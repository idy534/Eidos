import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApprovalMode, Item, Run, SessionSnapshot } from "../contracts.js";
import { deriveRuntimePresentation } from "../session-state.js";
import { AppShell } from "./AppShell.js";
import { isQueuedRunListRequest } from "../../../shared/queued-runs.js";

const fixture = vi.hoisted(() => ({
  snapshot: undefined as SessionSnapshot | undefined,
  draft: undefined as SessionSnapshot | undefined,
  forkSession: vi.fn(),
  startDraft: vi.fn(),
  reviseRun: vi.fn(),
}));
vi.mock("./useSessionController.js", () => ({
  useSessionController: () => [{
    snapshot: fixture.snapshot, draft: fixture.draft, projects: [], sessions: [], pending: {},
  }, { loadProjects: vi.fn(), loadSessions: vi.fn(), forkSession: fixture.forkSession, startDraft: fixture.startDraft }],
}));
vi.mock("./useRunController.js", () => ({
  useRunController: () => [{ composerMode: "idle", input: "", references: [], draftReady: false }, { reviseRun: fixture.reviseRun }],
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
vi.mock("./useExtensionController.js", () => ({
  useExtensionController: () => [{ plugins: [], skills: [], mcpServers: [], pendingAction: {} }, { load: vi.fn() }],
}));
vi.mock("./useGitReviewController.js", () => ({ useGitReviewController: () => [{}, {}] }));
vi.mock("./useResponseActionController.js", () => ({
  useResponseActionController: () => [{}, { load: vi.fn() }],
}));
vi.mock("./useContextUsageController.js", () => ({ useContextUsageController: () => [{}, {}] }));
vi.mock("./usePlanningState.js", () => ({ usePlanningState: () => ({}) }));
vi.mock("../components/AgentPanel.js", () => ({ useAgentState: () => ({}) }));
vi.mock("../components/SessionSidebar.js", () => ({
  SessionSidebar: ({ onOpenSettings }: { onOpenSettings: () => void }) => <button onClick={onOpenSettings}>打开设置</button>,
}));
vi.mock("../components/Composer.js", async () => {
  const { ApprovalModeSelector } = await import("../components/ApprovalModeSelector.js");
  return { Composer: ({ approvalMode, onApprovalModeChange }: {
    approvalMode: ApprovalMode; onApprovalModeChange: (mode: ApprovalMode) => void;
  }) => <ApprovalModeSelector mode={approvalMode} onChange={onApprovalModeChange} /> };
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
  return { ExecutionFeed: ({ runs, items, onFork, onRegenerate, onEditResend }: {
    runs: Run[]; items: Item[]; onFork?: (item: Item) => void;
    onRegenerate?: (run: Run) => void;
    onEditResend?: (run: Run, input: string) => void;
  }) => <>
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
    onNotification: vi.fn(() => () => undefined),
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
    "opens memory with a persisted Session or global scope for %s",
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
      const sessionId = kind === "persisted" ? persisted.session.id : null;
      const scope = kind === "persisted" ? "current" : "global";
      const memory = vi.fn(async (method: string, request: Record<string, unknown>) => {
        if (request.sessionId !== null && request.sessionId !== persisted.session.id) {
          throw new Error("EIDOS_RUNTIME_ERROR:MEMORY_SESSION_NOT_FOUND");
        }
        if (method === "memory/read") return {
          path: "MEMORY.md", scopeId: "global", revision: 0, currentRevision: 0,
          revisions: [], document: null, content: "# Memory", offset: 0, nextOffset: null,
        };
        return {
          scopes: [{ id: "global", kind: "global", mode: "auto", privacyEpoch: 0, generation: 0 }],
          documents: [], temporary: false,
        };
      });
      window.eidosRuntime.memory = memory as typeof window.eidosRuntime.memory;
      window.eidosRuntime.exportMemoryNotes = vi.fn(async () => {});
      window.eidosRuntime.listModelPresets = vi.fn(async () => ({ providers: [] }));
      renderShell();
      fireEvent.click(screen.getByRole("button", { name: "打开设置" }));
      fireEvent.click(screen.getByRole("tab", { name: "记忆" }));
      await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/read", expect.objectContaining({
        sessionId, scope, path: "MEMORY.md",
      })));
      expect(screen.queryByText("记忆无法读取，请重试。")).not.toBeInTheDocument();
      fireEvent.change(screen.getByRole("combobox", { name: "记忆模式" }), { target: { value: "read" } });
      await waitFor(() => expect(memory).toHaveBeenCalledWith("memory/policy", { sessionId, scope, mode: "read" }));
      await waitFor(() => expect(screen.getByRole("button", { name: "导出 Markdown" })).toBeEnabled());
      fireEvent.click(screen.getByRole("button", { name: "导出 Markdown" }));
      expect(window.eidosRuntime.exportMemoryNotes).toHaveBeenCalledWith(scope, sessionId ?? undefined);
    },
  );
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
    snapshot.session.project!.gitAvailable = true;
    renderShell();
    fireEvent.click(screen.getByRole("button", { name: "从这里分叉" }));
    const dialog = await screen.findByRole("alertdialog", { name: "从这里分叉" });
    const target = within(dialog).getByRole("radio", { name: "在独立工作树继续。" });
    fireEvent.click(target);
    fireEvent.click(within(dialog).getByRole("button", { name: "创建分叉" }));
    await waitFor(() => expect(fixture.forkSession).toHaveBeenCalledWith(snapshot.session.id, "selected", { kind: "new_worktree", state: "current_workspace" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: "从这里分叉" })).not.toBeInTheDocument());
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
