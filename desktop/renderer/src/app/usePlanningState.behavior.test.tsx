import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { PlanningReadResponse } from "../../../shared/planning.generated.js";
import { usePlanningState } from "./usePlanningState.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
afterEach(() => {
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

it("hides previous session data while loading or failing and ignores late responses", async () => {
  const a: PlanningReadResponse = {plans: [], questions: [{
    id: "request-a", sessionId: "a", runId: "run-a", itemId: "item-a",
    status: "pending", createdAt: 1, response: null,
    questions: [{id: "q", type: "text", question: "Question A"}],
  }]};
  let rejectB!: (error: Error) => void;
  let resolveA!: (state: PlanningReadResponse) => void;
  const readPlanning = vi.fn().mockResolvedValueOnce(a)
    .mockImplementationOnce(() => new Promise<PlanningReadResponse>((resolve) => {resolveA = resolve;}))
    .mockImplementationOnce(() => new Promise<PlanningReadResponse>((_resolve, reject) => {rejectB = reject;}));
  Object.defineProperty(window, "eidosRuntime", {configurable: true, value: {readPlanning}});
  const {result, rerender} = renderHook(({id}) => usePlanningState(id, true), {initialProps: {id: "a"}});
  await waitFor(() => expect(result.current.pendingQuestion?.id).toBe("request-a"));
  act(() => result.current.refresh());
  rerender({id: "b"});
  expect(result.current.state).toBeUndefined();
  await act(async () => { rejectB(new Error("Cannot load B")); });
  expect(result.current.pendingQuestion).toBeUndefined();
  expect(result.current.error).not.toBe("");
  await act(async () => { resolveA(a); });
  expect(result.current.state).toBeUndefined();
});
