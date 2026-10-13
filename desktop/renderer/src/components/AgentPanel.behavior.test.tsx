import { readFileSync } from "node:fs";
import path from "node:path";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CollaborationState } from "../../../shared/collaboration.generated.js";
import type { EidosRuntimeAPI, Item, Run, RuntimeNotification, SessionSnapshot } from "../contracts.js";
import {
  AgentList,
  AgentWorkspacePanel,
  formatAgentTooltip,
  useAgentState,
} from "./AgentPanel.js";

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

function historyAgent(sessionId = "child-session-history") {
  return {
    id: "agent-history",
    taskName: "inspect-history",
    role: "worker" as const,
    parentRunId: "parent-run",
    sessionId,
    runId: "child-run-latest",
    status: "succeeded" as const,
    task: "Read the full execution history",
    result: null,
    resultItemId: null,
    errorCode: null,
    createdAt: 1,
  };
}

function historyItem(
  id: string,
  runId: string,
  content: string,
  ordinal: number,
  kind: Item["kind"] = "assistant_message",
): Item {
  return {
    id,
    sessionId: "child-session-history",
    runId,
    kind,
    status: "completed",
    ordinal,
    content,
    createdAt: ordinal,
  };
}

function historyRun(id: string, createdAt: number): Run {
  return {
    id,
    sessionId: "child-session-history",
    status: "succeeded",
    allowedActions: [],
    modelId: "test-model",
    modelStepCount: 1,
    createdAt,
    updatedAt: createdAt,
  };
}

function historySnapshot(
  sessionId: string,
  items: Item[],
  runs: Run[],
  previousItemId?: string,
): SessionSnapshot {
  return {
    session: {
      id: sessionId,
      workspaceRoot: "/workspace",
      taskStatus: "completed",
      createdAt: 1,
      updatedAt: 10,
    },
    items,
    runs,
    stepResolutions: [],
    ...(previousItemId ? { previousItemId } : {}),
  };
}

describe("agent error labels", () => {
  it("uses the shared terminal cause instead of a raw error code", () => {
    expect(formatAgentTooltip({ ...historyAgent(), status: "failed", errorCode: "MODEL_PROTOCOL_ERROR",
      termination: { stage: "tool_validation", reason: "TOOL_ARGUMENT_CONTRACT_VIOLATION", toolName: "read_file", validationPath: "path" },
    })).toContain("模型调用“读取文件”时提供的“文件路径”参数不符合要求");
    expect(formatAgentTooltip({ ...historyAgent(), status: "stopped", stopReason: "completion_unconfirmed" })).toContain("任务已停止。");
  });
});

describe("AgentWorkspacePanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("marks the child that needs approval even when its transcript is closed", () => {
    const child = {
      id: "agent-approval", taskName: "fix-tests", role: "worker" as const,
      parentRunId: "parent-run", sessionId: "child-session", runId: "child-run",
      status: "waiting_approval" as const, task: "Run the tests", createdAt: 1,
    };
    const onOpen = vi.fn();
    render(<AgentList agents={[child]} onOpen={onOpen} approvalCounts={{ "child-session": 1 }} />);

    fireEvent.click(screen.getByRole("button", { name: /fix-tests.*待审批 1/ }));
    expect(onOpen).toHaveBeenCalledWith(child);
  });

  it.each([['default', 'default'], ['explorer', 'explorer'], ['worker', 'worker']] as const)(
    "shows the %s role in the agent list without claiming read-only permissions",
    async (role, label) => {
      const child = {
        id: 'agent-role', taskName: 'assigned-task', role,
        parentRunId: 'parent-run', sessionId: 'child-session', runId: 'child-run',
        status: 'running' as const, task: 'Investigate with inherited permissions', createdAt: 1,
      };
      const api: Partial<EidosRuntimeAPI> = {
        readSession: vi.fn().mockResolvedValue({ items: [], runs: [] }),
        onNotification: vi.fn().mockReturnValue(vi.fn()),
      };
      (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
      render(<>
        <AgentList agents={[child]} onOpen={() => undefined} />
        <AgentWorkspacePanel agents={[child]} agentId={child.id} error="" stopping={undefined}
          onOpen={() => undefined} onStop={() => undefined} approvals={[]}
          respondingApprovalIds={new Set()} respondingKindByApprovalId={{}} expiredApprovalIds={new Set()}
          errorsByApprovalId={{}} onApprove={() => undefined} onReject={() => undefined} />
      </>);
      expect(screen.getAllByText(label).length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('执行中').length).toBeGreaterThanOrEqual(1);
      expect(screen.queryByText(/只读探索/)).not.toBeInTheDocument();
    },
  );

  it("loads agents and stops an active child through the runtime API", async () => {
    const state: CollaborationState = {
      parentRunId: "parent-run",
      agents: [{
        id: "agent-1",
        taskName: "inspect-files",
        role: "explorer",
        parentRunId: "parent-run",
        sessionId: "child-session",
        runId: "child-run",
        status: "running",
        task: "Inspect files",
        result: null,
        resultItemId: null,
        errorCode: null,
        createdAt: 1,
      }],
      messages: [],
    };
    const api: Partial<EidosRuntimeAPI> = {
      readAgents: vi.fn().mockResolvedValue(state),
      stopAgent: vi.fn().mockResolvedValue(state),
      readSession: vi.fn().mockResolvedValue({ items: [], runs: [] }),
      onNotification: vi.fn().mockReturnValue(vi.fn()),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    function Fixture() {
      const controller = useAgentState("parent-session", true);
      return <>
        <AgentList agents={controller.state?.agents ?? []} onOpen={() => undefined} />
        <AgentWorkspacePanel agents={controller.state?.agents ?? []} agentId="agent-1"
          error={controller.error} stopping={controller.stopping}
          onOpen={() => undefined} onStop={(agent) => { void controller.stop(agent); }}
          approvals={[]} respondingApprovalIds={new Set()} respondingKindByApprovalId={{}}
          expiredApprovalIds={new Set()} errorsByApprovalId={{}}
          onApprove={() => undefined} onReject={() => undefined} />
      </>;
    }
    render(<Fixture />);

    expect((await screen.findAllByText("inspect-files")).length).toBe(2);
    expect(screen.getAllByText("explorer").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("执行中").length).toBeGreaterThanOrEqual(1);
    screen.getByRole("button", { name: "停止" }).click();

    await waitFor(() => expect(api.stopAgent).toHaveBeenCalledWith("parent-run", "agent-1"));
  });

  it("shows a child approval in the workspace with its source agent", async () => {
    const child = {
      id: "agent-approval", taskName: "fix-tests", role: "worker" as const,
      parentRunId: "parent-run", sessionId: "child-session", runId: "child-run",
      status: "waiting_approval" as const, task: "Run the tests", createdAt: 1,
    };
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockResolvedValue({
        items: [], runs: [{ id: "child-run", sessionId: "child-session", status: "waiting_approval",
          allowedActions: ["approve", "reject"], modelId: "test", modelStepCount: 1, createdAt: 1, updatedAt: 1 }],
      }),
      onNotification: vi.fn().mockReturnValue(vi.fn()),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    const onApprove = vi.fn();
    render(<AgentWorkspacePanel agents={[child]} agentId={child.id} error="" stopping={undefined}
      onOpen={() => undefined} onStop={() => undefined}
      approvals={[{ id: "approval-1", sessionId: child.sessionId, runId: child.runId,
        itemId: "item", toolCallId: "tool", summary: "执行测试命令", kind: "command_execution",
        command: "go test ./...", cwd: "/workspace", networkEnabled: false, timeoutSeconds: 30 }]}
      respondingApprovalIds={new Set()} respondingKindByApprovalId={{}} expiredApprovalIds={new Set()}
      errorsByApprovalId={{}} onApprove={onApprove} onReject={() => undefined} />);
    expect(await screen.findByText("执行测试命令")).toBeInTheDocument();
    expect(screen.getByText("fix-tests")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    expect(onApprove).toHaveBeenCalledWith(expect.objectContaining({ id: "approval-1" }));
  });

  it("renders subagent mission, execution feed, and handles back navigation", async () => {
    const child = {
      id: "agent-exec",
      taskName: "write-docs",
      role: "worker" as const,
      parentRunId: "parent-run",
      sessionId: "child-session-exec",
      runId: "child-run-exec",
      status: "succeeded" as const,
      task: "Write API documentation for auth module",
      result: "Successfully generated auth.md",
      resultItemId: null,
      errorCode: null,
      createdAt: 1000,
    };
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockResolvedValue({
        items: [
          {
            id: "child-msg-1",
            sessionId: child.sessionId,
            runId: child.runId,
            kind: "assistant_message",
            ordinal: 1,
            status: "completed",
            content: "I have updated the auth documentation.",
            createdAt: 1005,
          },
        ],
        runs: [
          {
            id: child.runId,
            sessionId: child.sessionId,
            status: "succeeded",
            allowedActions: [],
            modelId: "test-model",
            modelStepCount: 1,
            createdAt: 1000,
            updatedAt: 1010,
          },
        ],
      }),
      onNotification: vi.fn().mockReturnValue(vi.fn()),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    const onBackToList = vi.fn();
    render(
      <AgentWorkspacePanel
        agents={[child]}
        agentId={child.id}
        error=""
        stopping={undefined}
        onOpen={() => undefined}
        onStop={() => undefined}
        approvals={[]}
        onApprove={() => undefined}
        onReject={() => undefined}
        onBackToList={onBackToList}
      />,
    );

    expect(screen.getByText("write-docs")).toBeInTheDocument();
    expect((await screen.findAllByText("Write API documentation for auth module")).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText("I have updated the auth documentation.")).toBeInTheDocument();

    const backButton = screen.getByRole("button", { name: "返回子智能体列表" });
    fireEvent.click(backButton);
    expect(onBackToList).toHaveBeenCalled();
  });

  it("loads every history page and keeps items in chronological order", async () => {
    const child = historyAgent();
    const latestPage = historySnapshot(child.sessionId, [
      historyItem("new-user", "child-run-latest", "Latest task", 3, "user_message"),
      historyItem("new-1", "child-run-latest", "Newest answer", 4),
      historyItem("new-2", "child-run-latest", "Latest follow-up", 5),
    ], [historyRun("child-run-latest", 4)], "cursor-before-newest");
    const olderPage = historySnapshot(child.sessionId, [
      historyItem("old-user", "child-run-old", "Earlier task", 0, "user_message"),
      historyItem("old-1", "child-run-old", "Earlier answer", 1),
      historyItem("old-2", "child-run-old", "Earlier follow-up", 2),
    ], [historyRun("child-run-old", 1)]);
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockResolvedValueOnce(latestPage).mockResolvedValueOnce(olderPage),
      onNotification: vi.fn().mockReturnValue(vi.fn()),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    const { container } = render(
      <AgentWorkspacePanel agents={[child]} agentId={child.id} error="" stopping={undefined}
        onOpen={() => undefined} onStop={() => undefined} approvals={[]}
        onApprove={() => undefined} onReject={() => undefined} />,
    );

    expect(await screen.findByText("Earlier answer")).toBeInTheDocument();
    expect(screen.getByText("Earlier follow-up")).toBeInTheDocument();
    expect(screen.getByText("Newest answer")).toBeInTheDocument();
    expect(screen.getByText("Latest follow-up")).toBeInTheDocument();
    expect(api.readSession).toHaveBeenNthCalledWith(1, child.sessionId, { itemLimit: 500 });
    expect(api.readSession).toHaveBeenNthCalledWith(2, child.sessionId, {
      itemLimit: 500,
      beforeItemId: "cursor-before-newest",
    });

    const feedText = container.querySelector(".feed")?.textContent ?? "";
    expect(feedText.indexOf("Earlier answer")).toBeLessThan(feedText.indexOf("Earlier follow-up"));
    expect(feedText.indexOf("Earlier follow-up")).toBeLessThan(feedText.indexOf("Newest answer"));
    expect(feedText.indexOf("Newest answer")).toBeLessThan(feedText.indexOf("Latest follow-up"));
  });

  it("refreshes only for the current child session and coalesces notification bursts", async () => {
    const child = historyAgent();
    const initial = historySnapshot(child.sessionId, [
      historyItem("initial", child.runId, "Before refresh", 1),
    ], [historyRun(child.runId, 1)]);
    const refreshed = historySnapshot(child.sessionId, [
      historyItem("refreshed", child.runId, "After refresh", 2),
    ], [historyRun(child.runId, 1)]);
    let notify: ((notification: RuntimeNotification) => void) | undefined;
    const unsubscribe = vi.fn();
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockResolvedValueOnce(initial).mockResolvedValueOnce(refreshed),
      onNotification: vi.fn((callback: Parameters<EidosRuntimeAPI["onNotification"]>[0]) => {
        notify = callback;
        return unsubscribe;
      }),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    const view = render(
      <AgentWorkspacePanel agents={[child]} agentId={child.id} error="" stopping={undefined}
        onOpen={() => undefined} onStop={() => undefined} approvals={[]}
        onApprove={() => undefined} onReject={() => undefined} />,
    );
    expect(await screen.findByText("Before refresh")).toBeInTheDocument();

    notify?.({ method: "session/titleUpdated", params: { sessionId: "another-child", title: "Other" } });
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(api.readSession).toHaveBeenCalledTimes(1);

    const currentSessionUpdate: RuntimeNotification = {
      method: "session/titleUpdated",
      params: { sessionId: child.sessionId, title: "Updated" },
    };
    notify?.(currentSessionUpdate);
    notify?.(currentSessionUpdate);
    expect(await screen.findByText("After refresh", {}, { timeout: 1_000 })).toBeInTheDocument();
    expect(api.readSession).toHaveBeenCalledTimes(2);

    view.unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("runs one queued refresh after a notification arrives during a page read", async () => {
    const child = historyAgent();
    const initial = historySnapshot(child.sessionId, [
      historyItem("initial", child.runId, "Before queued refresh", 1),
    ], [historyRun(child.runId, 1)]);
    const refreshed = historySnapshot(child.sessionId, [
      historyItem("refreshed", child.runId, "After queued refresh", 2),
    ], [historyRun(child.runId, 1)]);
    let resolveInitial!: (snapshot: SessionSnapshot) => void;
    const initialRead = new Promise<SessionSnapshot>((resolve) => { resolveInitial = resolve; });
    let notify: ((notification: RuntimeNotification) => void) | undefined;
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockReturnValueOnce(initialRead).mockResolvedValueOnce(refreshed),
      onNotification: vi.fn((callback: Parameters<EidosRuntimeAPI["onNotification"]>[0]) => {
        notify = callback;
        return vi.fn();
      }),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    render(
      <AgentWorkspacePanel agents={[child]} agentId={child.id} error="" stopping={undefined}
        onOpen={() => undefined} onStop={() => undefined} approvals={[]}
        onApprove={() => undefined} onReject={() => undefined} />,
    );
    notify?.({ method: "session/titleUpdated", params: { sessionId: child.sessionId, title: "Updated" } });
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(api.readSession).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveInitial(initial);
      await initialRead;
    });
    expect(await screen.findByText("After queued refresh", {}, { timeout: 1_500 })).toBeInTheDocument();
    expect(api.readSession).toHaveBeenCalledTimes(2);
  });

  it("keeps the latest page and reports when an older history page fails", async () => {
    const child = historyAgent();
    const latestPage = historySnapshot(child.sessionId, [
      historyItem("latest-user", child.runId, "Current task", 1, "user_message"),
      historyItem("latest-answer", child.runId, "Current answer", 2),
    ], [historyRun(child.runId, 1)], "cursor-before-current");
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockResolvedValueOnce(latestPage).mockRejectedValueOnce(new Error("page unavailable")),
      onNotification: vi.fn().mockReturnValue(vi.fn()),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    render(
      <AgentWorkspacePanel agents={[child]} agentId={child.id} error="" stopping={undefined}
        onOpen={() => undefined} onStop={() => undefined} approvals={[]}
        onApprove={() => undefined} onReject={() => undefined} />,
    );

    expect(await screen.findByText("Current answer")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("子智能体执行记录未能全部加载。");
    expect(screen.getByText("Current answer")).toBeInTheDocument();
    expect(api.readSession).toHaveBeenCalledTimes(2);
  });

  it("renders overview with empty state when no agents exist", () => {
    render(
      <AgentWorkspacePanel
        agents={[]}
        agentId={undefined}
        error=""
        stopping={undefined}
        onOpen={() => undefined}
        onStop={() => undefined}
        approvals={[]}
        onApprove={() => undefined}
        onReject={() => undefined}
      />,
    );

    expect(screen.getByText("暂无子智能体任务")).toBeInTheDocument();
    expect(screen.getByText("0 个任务")).toBeInTheDocument();
  });

  it("renders compact mode with name, status, and task description in hover tooltip", () => {
    const child = {
      id: "agent-popover",
      taskName: "analyze-repo",
      role: "explorer" as const,
      parentRunId: "parent-run",
      sessionId: "child-session-popover",
      runId: "child-run-popover",
      status: "running" as const,
      task: "Analyze repository dependencies and report circular references",
      result: null,
      resultItemId: null,
      errorCode: null,
      createdAt: 2000,
    };
    const onOpen = vi.fn();
    render(
      <AgentList
        agents={[child]}
        onOpen={onOpen}
        compact
      />,
    );

    // Visible elements: task name, role pill, and status
    expect(screen.getByText("analyze-repo")).toBeInTheDocument();
    expect(screen.getByText("执行中")).toBeInTheDocument();
    const rolePill = screen.getByText("explorer");
    expect(rolePill).toBeInTheDocument();
    expect(rolePill).toHaveClass("agent-role-pill", "agent-role-pill--explorer");

    // Task description is not rendered in layout (preventing visual clutter)
    expect(screen.queryByText("Analyze repository dependencies and report circular references")).not.toBeInTheDocument();

    // Hover tooltip (title attribute) contains task description and role
    const button = screen.getByRole("button", { name: /analyze-repo.*执行中/ });
    expect(button).toHaveAttribute("title");
    expect(button.getAttribute("title")).toContain("任务描述：Analyze repository dependencies and report circular references");
    expect(button.getAttribute("title")).toContain("analyze-repo (explorer · 执行中)");

    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalledWith(child);
  });

  it("does not render redundant mission/result boxes or refresh button, and suppresses feedback and retry buttons in feed", async () => {
    const child = {
      id: "agent-clean-detail",
      taskName: "write-docs",
      role: "worker" as const,
      parentRunId: "parent-run",
      sessionId: "child-session-clean",
      runId: "child-run-clean",
      status: "succeeded" as const,
      task: "Write API documentation for auth module",
      result: "Successfully generated auth.md",
      resultItemId: null,
      errorCode: null,
      createdAt: 1000,
    };
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockResolvedValue({
        items: [
          {
            id: "child-msg-1",
            sessionId: child.sessionId,
            runId: child.runId,
            kind: "assistant_message",
            ordinal: 1,
            status: "completed",
            content: "I have updated the auth documentation.",
            createdAt: 1005,
          },
        ],
        runs: [
          {
            id: child.runId,
            sessionId: child.sessionId,
            status: "succeeded",
            allowedActions: [],
            modelId: "test-model",
            modelStepCount: 1,
            createdAt: 1000,
            updatedAt: 1010,
          },
        ],
      }),
      onNotification: vi.fn().mockReturnValue(vi.fn()),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    render(
      <AgentWorkspacePanel
        agents={[child]}
        agentId={child.id}
        error=""
        stopping={undefined}
        onOpen={() => undefined}
        onStop={() => undefined}
        approvals={[]}
        onApprove={() => undefined}
        onReject={() => undefined}
      />,
    );

    expect(await screen.findByText("I have updated the auth documentation.")).toBeInTheDocument();

    // 1. Redundant boxes and manual refresh button are removed
    expect(screen.queryByText("委托任务")).not.toBeInTheDocument();
    expect(screen.queryByText("交付成果摘要")).not.toBeInTheDocument();
    expect(screen.queryByText("刷新记录")).not.toBeInTheDocument();

    // 2. Feedback and retry buttons are removed from subagent feed
    expect(screen.queryByRole("button", { name: "点赞" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "差评" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新回答" })).not.toBeInTheDocument();
  });

  it("normalizes and renders single-line task snippet starting from the first character", () => {
    const child = {
      id: "agent-multiline",
      taskName: "count-lines",
      role: "worker" as const,
      parentRunId: "parent-run",
      sessionId: "child-session-multiline",
      runId: "child-run-multiline",
      status: "running" as const,
      task: "\n  1. 负责统计模块代码行数\n  2. 排除纯空白行与纯注释行\n",
      result: null,
      resultItemId: null,
      errorCode: null,
      createdAt: 1000,
    };
    render(<AgentList agents={[child]} onOpen={() => undefined} />);

    const snippet = screen.getByText("1. 负责统计模块代码行数 2. 排除纯空白行与纯注释行");
    expect(snippet).toBeInTheDocument();
    expect(snippet).toHaveClass("agent-card__snippet");
  });

  it("does not render errorCode pills in card footer or error banner in detail view", async () => {
    const child = {
      id: "agent-interrupted",
      taskName: "count-internal-app",
      role: "worker" as const,
      parentRunId: "parent-run",
      sessionId: "child-session-interrupted",
      runId: "child-run-interrupted",
      status: "interrupted" as const,
      task: "Count Go lines",
      result: "Done",
      resultItemId: null,
      errorCode: "RUNTIME_INTERRUPTED",
      createdAt: 1000,
    };
    const api: Partial<EidosRuntimeAPI> = {
      readSession: vi.fn().mockResolvedValue({ items: [], runs: [] }),
      onNotification: vi.fn().mockReturnValue(vi.fn()),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;

    render(<>
      <AgentList agents={[child]} onOpen={() => undefined} />
      <AgentWorkspacePanel
        agents={[child]}
        agentId={child.id}
        error=""
        stopping={undefined}
        onOpen={() => undefined}
        onStop={() => undefined}
        approvals={[]}
        onApprove={() => undefined}
        onReject={() => undefined}
      />
    </>);

    // Status pill in header/card top is present
    expect(screen.getAllByText("已中断").length).toBeGreaterThanOrEqual(1);

    // Error code label (such as 运行时中断 or 未确认完成) is NOT rendered in card footer or detail banner
    expect(screen.queryByText("运行时中断")).not.toBeInTheDocument();
  });

  it("enforces fixed height, flex-shrink 0, and scrollbar styles on agent cards to prevent compression", () => {
    const agentsCss = readFileSync(
      path.resolve(process.cwd(), "desktop/renderer/src/components/agents.css"),
      "utf8",
    );

    // .agent-card has fixed height, min-height, and flex-shrink 0
    expect(agentsCss).toMatch(/\.agent-card\s*\{[^}]*height:\s*86px;/s);
    expect(agentsCss).toMatch(/\.agent-card\s*\{[^}]*min-height:\s*86px;/s);
    expect(agentsCss).toMatch(/\.agent-card\s*\{[^}]*flex:\s*0 0 86px;/s);
    expect(agentsCss).toMatch(/\.agent-card\s*\{[^}]*flex-shrink:\s*0;/s);

    // .agent-list has vertical scroll and custom scrollbar
    expect(agentsCss).toMatch(/\.agent-list\s*\{[^}]*overflow-y:\s*auto;/s);
    expect(agentsCss).toMatch(/\.agent-list\s*\{[^}]*overflow-x:\s*hidden;/s);
    // The common skin owns appearance; agent and dock styles own scrolling layout.
    expect(agentsCss).not.toContain("scrollbar-width: thin");

    // .environment-popover__panel has max-height and overflow: hidden, with .environment-popover__body handling overflow-y: auto and custom scrollbar
    const dockCss = readFileSync(
      path.resolve(process.cwd(), "desktop/renderer/src/components/WorkspaceDock.css"),
      "utf8",
    );
    expect(dockCss).toMatch(/\.environment-popover__panel\s*\{[^}]*max-height:\s*min\(28rem,\s*calc\(100vh\s*-\s*5rem\)\);/s);
    expect(dockCss).toMatch(/\.environment-popover__panel\s*\{[^}]*overflow:\s*hidden;/s);
    expect(dockCss).toMatch(/\.environment-popover__body\s*\{[^}]*overflow-y:\s*auto;/s);
    expect(dockCss).toMatch(/\.environment-popover__body\s*\{[^}]*overflow-x:\s*hidden;/s);
    expect(dockCss).not.toContain("scrollbar-width: thin");
    const scrollbarCss = readFileSync(
      path.resolve(process.cwd(), "desktop/renderer/src/styles/scrollbars.css"),
      "utf8",
    );
    expect(scrollbarCss).toMatch(/::-webkit-scrollbar\s*\{[^}]*width:\s*var\(--scrollbar-size\);/s);
  });
});
