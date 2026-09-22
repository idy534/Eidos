import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EidosRuntimeAPI } from "../contracts.js";
import type { PlanDocument, UserInputRequest } from "../../../shared/planning.generated.js";
import { PlanningPanel } from "./PlanningPanel.js";

const plan: PlanDocument = {
  id: "plan-1",
  sessionId: "session-1",
  runId: "run-plan",
  revision: 1,
  title: "Plan one",
  markdown: "# Plan one\n\n- inspect",
  sha256: "a".repeat(64),
  status: "review",
  path: "/tmp/plan.md",
  updatedAt: 1,
  executionRunId: null,
};

const question: UserInputRequest = {
  id: "question-1",
  sessionId: "session-1",
  runId: "run-plan",
  itemId: "item-question",
  questions: [{
    id: "scope",
    question: "Which scope?",
    type: "single_select",
    options: [{ id: "runtime", label: "Runtime" }, { id: "desktop", label: "Desktop" }],
    recommendedOptionId: "runtime",
  }],
  status: "pending",
  response: null,
  createdAt: 1,
};

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("PlanningPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setup(state: { plans: PlanDocument[]; questions: UserInputRequest[] }) {
    const api: Partial<EidosRuntimeAPI> = {
      readPlanning: vi.fn().mockResolvedValue(state),
      onNotification: vi.fn().mockReturnValue(() => {}),
      answerUserInput: vi.fn().mockResolvedValue(question),
      openPlan: vi.fn().mockResolvedValue(undefined),
      readPlan: vi.fn().mockResolvedValue(plan),
      editPlan: vi.fn().mockResolvedValue({ ...plan, revision: 2, markdown: "# Updated" }),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  it("submits a selected clarification answer", async () => {
    const api = setup({ plans: [], questions: [question] });
    render(<PlanningPanel sessionId="session-1" ready canEdit onExecute={vi.fn()} onRevise={vi.fn()} />);

    await screen.findByText("需要你补充信息");
    fireEvent.click(screen.getByRole("radio", { name: "Runtime（推荐）" }));
    fireEvent.click(screen.getByRole("button", { name: "提交回答" }));

    await waitFor(() => expect(api.answerUserInput).toHaveBeenCalledWith({
      requestId: "question-1",
      response: { status: "answered", answers: [{ questionId: "scope", optionIds: ["runtime"] }] },
    }));
  });

  it("edits and confirms a reviewable plan", async () => {
    const onExecute = vi.fn().mockResolvedValue(true);
    const api = setup({ plans: [plan], questions: [] });
    render(<PlanningPanel sessionId="session-1" ready canEdit onExecute={onExecute} onRevise={vi.fn()} />);

    await screen.findByRole("heading", { level: 3, name: /Plan one/ });
    fireEvent.click(screen.getByRole("button", { name: "编辑计划" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown 计划" }), { target: { value: "# Updated" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(api.editPlan).toHaveBeenCalledWith({
      planId: "plan-1", expectedRevision: 1, markdown: "# Updated",
    }));

    fireEvent.click(await screen.findByRole("button", { name: "确认并执行此版本" }));
    await waitFor(() => expect(onExecute).toHaveBeenCalledWith(plan));
  });
});
