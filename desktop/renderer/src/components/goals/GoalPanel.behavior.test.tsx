import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { GoalRequest, GoalView, SessionGoal } from "../../../../shared/goals.js";
import type { RuntimeNotification } from "../../../contracts.js";
import { GoalPanel } from "./GoalPanel.js";
import { useGoal } from "./useGoal.js";

const sessionId = "11111111-1111-1111-1111-111111111111";
const goal: SessionGoal = { id: "22222222-2222-2222-2222-222222222222", sessionId, objective: "完成登录模块的全部验收。", status: "active",
  revision: 1, objectiveRevision: 1, activation: 1, model: { modelId: "model" }, approvalMode: "manual", createdAt: 1, updatedAt: 1 };
let current: GoalView;
let listeners: ((notice: RuntimeNotification) => void)[];
const request = vi.fn(async (command: GoalRequest): Promise<GoalView> => {
  if (command.action === "pause") current = { ...current, goal: { ...current.goal!, status: "paused", revision: 2 } };
  if (command.action === "clear") current = { ...current, goal: { ...current.goal!, status: "cleared", revision: 3 } };
  return current;
});
function Harness({ id = sessionId }: { id?: string }) {
  const state = useGoal(id, true);
  return <GoalPanel view={state.view} ready busy={state.busy} error={state.error} activeRun={undefined}
    onAction={(action) => void state.execute({ action, expectedRevision: state.view.goal!.revision })}
    onRefresh={() => void state.refresh(true)} />;
}
beforeEach(() => {
  current = { goal, tokensUsed: 2000, runCount: 2, effectiveMs: 60000 };
  listeners = []; request.mockClear();
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { goal: request,
    onNotification: (listener: (notice: RuntimeNotification) => void) => { listeners.push(listener); return () => { listeners = listeners.filter((value) => value !== listener); }; } } });
});

it("renders no separate creation entry or dialog when there is no goal", async () => {
  current = {};
  const { container } = render(<Harness />);
  await waitFor(() => expect(request).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
  expect(screen.queryByRole("alertdialog")).toBeNull();
});

it("pauses continuation and follows authoritative completion evidence", async () => {
  render(<Harness />);
  await userEvent.click(await screen.findByRole("button", { name: "暂停续跑" }));
  await screen.findByRole("button", { name: "继续目标" });
  expect(request).toHaveBeenCalledWith(expect.objectContaining({ action: "pause", expectedRevision: 1, sessionId }));
  current = { ...current, goal: { ...goal, status: "complete", revision: 3, statusDetail: "所有验收通过。", evidence: [{ requirement: "登录测试", proof: "相关测试通过。" }] } };
  act(() => { for (const listener of listeners) listener({ method: "goal/updated", params: { sessionId } }); });
  await userEvent.click(await screen.findByRole("button", { name: /已完成/ }));
  expect(screen.getByText("相关测试通过。")).toBeInTheDocument();
  expect(screen.queryByText(/Token 预算/)).toBeNull();
});

it("resumes without a budget editor or confirmation form", async () => {
  current = { goal: { ...goal, status: "paused" } };
  render(<Harness />);
  await userEvent.click(await screen.findByRole("button", { name: "继续目标" }));
  await waitFor(() => expect(request).toHaveBeenCalledWith(expect.objectContaining({ action: "resume", expectedRevision: 1 })));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("spinbutton")).toBeNull();
});

it("clears the saved goal without bringing back a creation button", async () => {
  render(<Harness />);
  await userEvent.click(await screen.findByRole("button", { name: /目标 · 进行中/ }));
  await userEvent.click(screen.getByRole("button", { name: "清除目标" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "目标执行状态" })).toBeNull());
  expect(screen.queryByText("设定目标")).toBeNull();
});

it("keeps action errors visible until an explicit successful refresh", async () => {
  render(<Harness />);
  await screen.findByRole("button", { name: "暂停续跑" });
  request.mockImplementationOnce(async () => { throw new Error("EIDOS_RUNTIME_ERROR:GOAL_REVISION_CONFLICT"); });
  await userEvent.click(screen.getByRole("button", { name: "暂停续跑" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("目标已变化");
  await userEvent.click(screen.getByRole("button", { name: "重新读取" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});

it("does not show another Session's goal while requests finish out of order", async () => {
  let finish!: (value: GoalView) => void;
  request.mockImplementationOnce(() => new Promise<GoalView>((resolve) => { finish = resolve; }));
  const { rerender } = render(<Harness />);
  const other = "33333333-3333-3333-3333-333333333333";
  current = { goal: { ...goal, sessionId: other, objective: "另一个会话的目标。" } };
  rerender(<Harness id={other} />);
  await userEvent.click(await screen.findByRole("button", { name: /目标 · 进行中/ }));
  await act(async () => finish({ goal }));
  expect(screen.getByText("另一个会话的目标。")).toBeInTheDocument();
  expect(screen.queryByText(goal.objective)).toBeNull();
});
