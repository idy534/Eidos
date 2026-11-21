import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AgentChanges, AgentSummary } from "../../../shared/collaboration.generated.js";
import { AgentChangesView, AgentMessageComposer, formatAgentUsage } from "./AgentWorkspaceTools.js";

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

function agent(overrides: Partial<AgentSummary> = {}): AgentSummary {
  return {
    id: "agent-1", taskName: "edit-parser", role: "worker", parentRunId: "parent-run", sessionId: "child-session",
    runId: "child-run", status: "succeeded", task: "Edit the parser", createdAt: 1, workspaceMode: "worktree",
    ...overrides,
  };
}

function installRuntime(runtime: Record<string, unknown>) {
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: runtime });
}

afterEach(() => {
  if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
  else Reflect.deleteProperty(window, "eidosRuntime");
});

describe("AgentMessageComposer", () => {
  it("sends a user instruction and keeps the operation id when a send fails", async () => {
    const messageAgent = vi.fn()
      .mockRejectedValueOnce(new Error("busy"))
      .mockResolvedValueOnce({ agents: [], messages: [] });
    installRuntime({ messageAgent });
    render(<AgentMessageComposer agent={agent()} />);
    const input = screen.getByLabelText("发给 edit-parser");
    fireEvent.change(input, { target: { value: "Focus on errors." } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(messageAgent).toHaveBeenCalledTimes(1));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(messageAgent).toHaveBeenCalledTimes(2));
    const [first, second] = messageAgent.mock.calls.map((call) => call[0]);
    expect(second).toEqual(first);
    expect(first).toMatchObject({ parentRunId: "parent-run", agentId: "agent-1", text: "Focus on errors." });
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  });
});

describe("AgentChangesView", () => {
  const changes: AgentChanges = {
    agentId: "agent-1", status: "ready", diff: "diff --git a/a.txt b/a.txt\n-five\n+FIVE\n", diffNextOffset: 40, diffHasMore: false,
    files: [{ path: "a.txt", kind: "modified", additions: 1, deletions: 1, binary: false }],
  };

  it("lists changes of an idle isolated child and discards after confirmation", async () => {
    const readAgentChanges = vi.fn().mockResolvedValue(changes);
    const discardAgentWorkspace = vi.fn().mockResolvedValue({ agents: [], messages: [] });
    installRuntime({ readAgentChanges, discardAgentWorkspace });
    render(<AgentChangesView agent={agent()} />);
    expect(await screen.findByText("a.txt")).toBeTruthy();
    expect(screen.getByText("+1 −1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "放弃" }));
    expect(discardAgentWorkspace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认放弃" }));
    await waitFor(() => expect(discardAgentWorkspace).toHaveBeenCalledWith("parent-run", "agent-1"));
  });

  it("stays hidden for shared or running children", () => {
    const readAgentChanges = vi.fn();
    installRuntime({ readAgentChanges });
    const { container, rerender } = render(<AgentChangesView agent={agent({ workspaceMode: "shared" })} />);
    rerender(<AgentChangesView agent={agent({ status: "running" })} />);
    expect(container.textContent).toBe("");
    expect(readAgentChanges).not.toHaveBeenCalled();
  });
});

describe("formatAgentUsage", () => {
  it("marks incomplete totals as a lower bound", () => {
    expect(formatAgentUsage(agent({ usage: { inputTokens: 1200, outputTokens: 300, incomplete: true } }))).toBe("≥1.5k tokens");
    expect(formatAgentUsage(agent())).toBeUndefined();
  });
});
