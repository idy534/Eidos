import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { TeamMethods, TeamProposalView, TeamSessionState, TeamView } from "../../../../shared/teams.js";
import type { RuntimeNotification } from "../../../contracts.js";
import { TeamPanel } from "./TeamPanel.js";
import { TeamRoster } from "./TeamRoster.js";
import { useTeam } from "./useTeam.js";

const sessionId = "11111111-1111-1111-1111-111111111111";
const team: TeamView = {
  id: "team-1", leadSessionId: sessionId, status: "active", objective: "重构登录模块", autoWakeLead: true,
  runSettings: { modelId: "model-a" }, version: 1, createdAt: 1, updatedAt: 1,
  members: [
    { id: "m-1", name: "builder", role: "worker", brief: "实现接口", modelId: "model-a", workspaceMode: "worktree",
      status: "active", sessionId: "member-session-1", runId: "run-b", runStatus: "running", createdAt: 1 },
    { id: "m-2", name: "critic", role: "verifier", brief: "审查", modelId: "model-a", workspaceMode: "shared",
      status: "active", sessionId: "member-session-2", createdAt: 1 },
  ],
  tasks: [
    { id: "t-1", key: "api.1", title: "接口", status: "in_review", owner: "builder", reviewer: "lead", milestone: "M1",
      evidence: [{ toolCallId: "call-1" }], version: 2, createdBy: "lead", updatedAt: 2 },
    { id: "t-2", key: "gate.1.critic", kind: "gate", title: "Gate: M1", status: "open", owner: "critic", blocked: true,
      milestone: "M1", version: 1, createdBy: "system", updatedAt: 2 },
  ],
};
let state: TeamSessionState;
let listeners: ((notice: RuntimeNotification) => void)[];
const calls: { method: string; request: unknown }[] = [];
const runtime = vi.fn(async (method: keyof TeamMethods, request: unknown): Promise<unknown> => {
  calls.push({ method, request });
  if (method === "team/read") return state;
  if (method === "team/messages") {
    if (((request as { after?: number }).after ?? 0) >= 1) return { messages: [], nextCursor: 1, hasMore: false };
    return { messages: [{ id: "msg-1", seq: 1, channel: "direct", author: "builder", authorKind: "member", recipients: ["lead"],
      content: "接口已完成", createdAt: 1 }], nextCursor: 1, hasMore: false };
  }
  if (method === "team/memberChanges") return { member: "builder", status: "ready", files: [] };
  return team;
});

function Harness() {
  const value = useTeam(sessionId, true);
  if (!value.team) return null;
  return <TeamPanel team={value.team} usage={value.usage} actions={value} onOpenMember={() => {}} onBack={() => {}}
    onCancelRun={() => {}} approvals={[]} onApprove={() => {}} onReject={() => {}} />;
}

beforeEach(() => {
  state = { team, usage: [{ member: "builder", inputTokens: 1500, outputTokens: 500, incomplete: true }] };
  listeners = [];
  calls.length = 0;
  runtime.mockClear();
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: {
    team: runtime,
    onNotification: (listener: (notice: RuntimeNotification) => void) => {
      listeners.push(listener);
      return () => { listeners = listeners.filter((value) => value !== listener); };
    },
  } });
});

it("shows the board, posts with an operation id and refreshes on team notices", async () => {
  render(<Harness />);
  expect(await screen.findByText("接口已完成")).toBeInTheDocument();
  expect(screen.getByText("≥2.0k tokens")).toBeInTheDocument();
  await userEvent.type(screen.getByRole("textbox", { name: "发给 lead" }), "先合并接口{Enter}");
  await waitFor(() => expect(calls.some((call) => call.method === "team/post")).toBe(true));
  const post = calls.find((call) => call.method === "team/post")!.request as { operationId: string; message: unknown };
  expect(post.operationId).toMatch(/.+/);
  expect(post.message).toEqual({ to: ["lead"], channel: "board", text: "先合并接口" });
  const reads = calls.filter((call) => call.method === "team/read").length;
  act(() => { for (const listener of listeners) listener({ method: "team/updated", params: { sessionId } }); });
  await waitFor(() => expect(calls.filter((call) => call.method === "team/read").length).toBeGreaterThan(reads));
  // A member Run edge refreshes the lead's view too.
  const before = calls.filter((call) => call.method === "team/read").length;
  act(() => { for (const listener of listeners) listener({ method: "run/completed", params: { sessionId: "member-session-1", run: {} as never } }); });
  await waitFor(() => expect(calls.filter((call) => call.method === "team/read").length).toBeGreaterThan(before));
});

it("accepts and rejects tasks under review and marks gates", async () => {
  render(<Harness />);
  await userEvent.click(await screen.findByRole("tab", { name: "任务" }));
  expect(screen.getByText("关口")).toBeInTheDocument();
  const review = screen.getByRole("region", { name: "待审查" });
  await userEvent.click(within(review).getByRole("button", { name: "退回" }));
  const reason = screen.getByRole("textbox", { name: "原因" });
  await userEvent.type(reason, "缺少测试");
  await userEvent.click(within(review).getAllByRole("button", { name: "退回" })[0]!);
  await waitFor(() => expect(calls.some((call) => call.method === "team/taskUpdate")).toBe(true));
  expect((calls.find((call) => call.method === "team/taskUpdate")!.request as { request: unknown }).request)
    .toEqual({ op: "reject", key: "api.1", note: "缺少测试" });
});

it("lists members with state and confirms before dissolving", async () => {
  render(<Harness />);
  await userEvent.click(await screen.findByRole("tab", { name: "成员" }));
  expect(screen.getByRole("button", { name: /builder/ })).toHaveTextContent("正在执行");
  expect(screen.getByRole("button", { name: /critic/ })).toHaveTextContent("空闲");
  await userEvent.click(screen.getByRole("button", { name: "解散" }));
  expect(calls.some((call) => call.method === "team/setStatus")).toBe(false);
  await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "解散" }));
  await waitFor(() => expect(calls.find((call) => call.method === "team/setStatus")?.request)
    .toEqual(expect.objectContaining({ teamId: "team-1", status: "dissolving" })));
});

it("lets the user drop members and switch workspaces before confirming the roster", async () => {
  const proposal: TeamProposalView = {
    id: "proposal-1", sessionId, runId: "run-1", decision: "pending", createdAt: 1,
    proposal: { objective: "重构登录模块", members: [
      { name: "builder", role: "worker", brief: "实现接口", workspace: "worktree" },
      { name: "critic", role: "verifier", brief: "审查", workspace: "shared" },
    ], tasks: [{ key: "api.1", title: "接口", owner: "builder" }] },
  };
  const decide = vi.fn();
  render(<TeamRoster proposal={proposal} approvalMode="manual" modelLabel="Model A" worktreeAvailable busy={false}
    error={undefined} onDecide={decide} />);
  await userEvent.click(screen.getByRole("checkbox", { name: "保留 critic" }));
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "builder 的工作区" }), "shared");
  await userEvent.click(screen.getByRole("checkbox", { name: /自动续跑/ }));
  await userEvent.click(screen.getByRole("button", { name: "组建团队" }));
  expect(decide).toHaveBeenCalledWith({ confirmed: true, removedMembers: ["critic"], workspaces: { builder: "shared" },
    approvalMode: "manual", autoWakeLead: false });
});

it("keeps every member in a shared workspace when the Session has no Git", () => {
  const proposal: TeamProposalView = {
    id: "proposal-1", sessionId, runId: "run-1", decision: "pending", createdAt: 1,
    proposal: { objective: "调研", members: [{ name: "scout", role: "explorer", brief: "调研", workspace: "worktree" }] },
  };
  const decide = vi.fn();
  render(<TeamRoster proposal={proposal} approvalMode="full_access" modelLabel={undefined} worktreeAvailable={false} busy={false}
    error={undefined} onDecide={decide} />);
  expect(screen.getByRole("combobox", { name: "scout 的工作区" })).toBeDisabled();
  screen.getByRole("button", { name: "组建团队" }).click();
  expect(decide).toHaveBeenCalledWith(expect.objectContaining({ workspaces: { scout: "shared" },
    approvalMode: "full_access", fullAccessConfirmation: "full-access-v1" }));
});
