import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Run } from "../contracts.js";
import type { UserInputRequest } from "../../../shared/planning.generated.js";
import { ComposerSlot } from "./ComposerSlot.js";

const baseRun: Run = {
  id: "run-1",
  sessionId: "session-1",
  status: "running",
  workMode: "plan",
  approvalMode: "manual",
  modelId: "gpt-4o",
  createdAt: 1,
  updatedAt: 1,
};

const pendingQuestion: UserInputRequest = {
  id: "req-1",
  sessionId: "session-1",
  runId: "run-1",
  itemId: "item-1",
  questions: [
    {
      id: "q1",
      question: "Confirm target architecture?",
      type: "single_select",
      options: [{ id: "opt1", label: "Microservices" }, { id: "opt2", label: "Monolith" }],
    },
  ],
  status: "pending",
  response: null,
  createdAt: 1,
};

describe("ComposerSlot", () => {
  it("renders children when run is normal and no pending input", () => {
    render(
      <ComposerSlot run={baseRun} approval={undefined}>
        <div data-testid="default-composer">Normal Composer</div>
      </ComposerSlot>,
    );

    expect(screen.getByTestId("default-composer")).toBeInTheDocument();
  });

  it("renders ClarificationComposer when pendingUserInput is present", () => {
    render(
      <ComposerSlot
        run={{ ...baseRun, status: "waiting_input" }}
        approval={undefined}
        pendingUserInput={pendingQuestion}
        userInputReady={true}
        onAnswerUserInput={vi.fn()}
      >
        <div data-testid="default-composer">Normal Composer</div>
      </ComposerSlot>,
    );

    expect(screen.queryByTestId("default-composer")).toBeNull();
    expect(screen.getByRole("region", { name: "澄清问题" })).toBeInTheDocument();
    expect(screen.getByText("Confirm target architecture?")).toBeInTheDocument();
  });

  it("resets answers when the clarification request changes", () => {
    const props = {run: {...baseRun, status: "waiting_input" as const}, approval: undefined};
    const {rerender} = render(
      <ComposerSlot {...props} pendingUserInput={pendingQuestion}>Composer</ComposerSlot>,
    );
    fireEvent.click(screen.getByRole("radio", {name: "Microservices"}));
    expect(screen.getByRole("button", {name: /提交回答/})).toBeEnabled();
    rerender(
      <ComposerSlot {...props} pendingUserInput={{...pendingQuestion, id: "req-2"}}>Composer</ComposerSlot>,
    );
    expect(screen.getByRole("radio", {name: "Microservices"})).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", {name: /提交回答/})).toBeDisabled();
  });

  it("renders ClarificationStatusBanner when waiting_input but question is not yet loaded", () => {
    render(
      <ComposerSlot
        run={{ ...baseRun, status: "waiting_input" }}
        approval={undefined}
        pendingUserInput={undefined}
      >
        <div data-testid="default-composer">Normal Composer</div>
      </ComposerSlot>,
    );

    expect(screen.queryByTestId("default-composer")).toBeNull();
    expect(screen.getByText("正在准备澄清问题…")).toBeInTheDocument();
    expect(screen.getByText("正在同步需要补充的信息")).toBeInTheDocument();
  });
});
