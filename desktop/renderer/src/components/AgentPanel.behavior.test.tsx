import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CollaborationState } from "../../../shared/collaboration.generated.js";
import type { EidosRuntimeAPI } from "../contracts.js";
import { AgentList, AgentWorkspacePanel, useAgentState } from "./AgentPanel.js";

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

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

    const backButton = screen.getByRole("button", { name: "返回子 Agent 列表" });
    fireEvent.click(backButton);
    expect(onBackToList).toHaveBeenCalled();
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

    expect(screen.getByText("暂无子 Agent 任务")).toBeInTheDocument();
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

    // Visible elements: task name and status
    expect(screen.getByText("analyze-repo")).toBeInTheDocument();
    expect(screen.getByText("执行中")).toBeInTheDocument();

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
});
