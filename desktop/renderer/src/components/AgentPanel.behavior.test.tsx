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
    expect(screen.getByText("探索 · 执行中")).toBeInTheDocument();
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
});
