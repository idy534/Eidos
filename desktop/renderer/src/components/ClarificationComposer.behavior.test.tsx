import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EidosRuntimeAPI } from "../contracts.js";
import type { UserInputRequest } from "../../../shared/planning.generated.js";
import { ClarificationComposer } from "./ClarificationComposer.js";

const singleQuestion: UserInputRequest = {
  id: "req-single",
  sessionId: "session-1",
  runId: "run-plan",
  itemId: "item-1",
  questions: [
    {
      id: "scope",
      question: "Which scope to apply?",
      type: "single_select",
      options: [
        { id: "runtime", label: "Runtime", description: "Backend runtime engine" },
        { id: "desktop", label: "Desktop", description: "Frontend desktop application" },
      ],
      recommendedOptionId: "runtime",
    },
  ],
  status: "pending",
  response: null,
  createdAt: 1,
};

const multiQuestions: UserInputRequest = {
  id: "req-multi",
  sessionId: "session-1",
  runId: "run-plan",
  itemId: "item-2",
  questions: [
    {
      id: "q1",
      question: "Choose target platforms",
      type: "multi_select",
      options: [
        { id: "mac", label: "macOS" },
        { id: "win", label: "Windows" },
      ],
    },
    {
      id: "q2",
      question: "Any additional constraints?",
      type: "text",
    },
  ],
  status: "pending",
  response: null,
  createdAt: 2,
};

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("ClarificationComposer", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupApi() {
    const api: Partial<EidosRuntimeAPI> = {
      answerUserInput: vi.fn().mockResolvedValue(singleQuestion),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  it("submits a selected clarification answer for a single question", async () => {
    const api = setupApi();
    const onSaved = vi.fn();

    render(<ClarificationComposer request={singleQuestion} ready onSaved={onSaved} />);

    expect(screen.getByRole("region", { name: "澄清问题" })).toBeInTheDocument();
    expect(screen.getByText("Which scope to apply?")).toBeInTheDocument();
    expect(screen.getByText("Runtime")).toBeInTheDocument();
    expect(screen.getByText("Desktop")).toBeInTheDocument();

    // Click "Desktop" option
    fireEvent.click(screen.getByText("Desktop"));

    // Submit answer
    fireEvent.click(screen.getByRole("button", { name: /提交回答/ }));

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-single",
        response: {
          status: "answered",
          answers: [
            {
              questionId: "scope",
              optionIds: ["desktop"],
              text: "",
            },
          ],
        },
      }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("supports multiple questions, question tab switching, and navigation", async () => {
    const api = setupApi();
    const onSaved = vi.fn();

    render(<ClarificationComposer request={multiQuestions} ready onSaved={onSaved} />);

    // Shows question 1
    expect(screen.getByText("Choose target platforms")).toBeInTheDocument();
    expect(screen.getByText(/多选 · 第 1 \/ 2 题/)).toBeInTheDocument();

    // Select "macOS" option for Q1
    fireEvent.click(screen.getByText("macOS"));

    // Switch to question 2 using the "下一个" button
    fireEvent.click(screen.getByRole("button", { name: "下一个" }));

    // Now on Q2
    expect(screen.getByText("Any additional constraints?")).toBeInTheDocument();
    expect(screen.getByText(/简答 · 第 2 \/ 2 题/)).toBeInTheDocument();

    // Enter text answer for Q2
    const textarea = screen.getByPlaceholderText("请输入你的回答…");
    fireEvent.change(textarea, { target: { value: "Must be lightweight" } });

    // Switch back to Q1 using the "上一个" button
    fireEvent.click(screen.getByRole("button", { name: "上一个" }));
    expect(screen.getByText("Choose target platforms")).toBeInTheDocument();
    expect(screen.getByText(/多选 · 第 1 \/ 2 题/)).toBeInTheDocument();

    // Submit answers
    fireEvent.click(screen.getByRole("button", { name: /提交回答/ }));

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-multi",
        response: {
          status: "answered",
          answers: [
            {
              questionId: "q1",
              optionIds: ["mac"],
              text: "",
            },
            {
              questionId: "q2",
              optionIds: [],
              text: "Must be lightweight",
            },
          ],
        },
      }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("submits skip response when user clicks skip on single question", async () => {
    const api = setupApi();
    const onSaved = vi.fn();

    render(<ClarificationComposer request={singleQuestion} ready onSaved={onSaved} />);

    fireEvent.click(screen.getByRole("button", { name: "跳过" }));

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-single",
        response: {
          status: "skipped",
          answers: [],
        },
      }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("skips only the current question in multiple questions and advances", async () => {
    const api = setupApi();
    const onSaved = vi.fn();

    render(<ClarificationComposer request={multiQuestions} ready onSaved={onSaved} />);

    // On Q1, click "跳过此题"
    fireEvent.click(screen.getByRole("button", { name: "跳过此题" }));

    // Advances to Q2 automatically
    expect(screen.getByText("Any additional constraints?")).toBeInTheDocument();
    expect(screen.getByText(/简答 · 第 2 \/ 2 题/)).toBeInTheDocument();

    // Answer Q2
    const textarea = screen.getByPlaceholderText("请输入你的回答…");
    fireEvent.change(textarea, { target: { value: "Must be lightweight" } });

    // Submit answers
    fireEvent.click(screen.getByRole("button", { name: /提交回答/ }));

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-multi",
        response: {
          status: "answered",
          answers: [
            {
              questionId: "q1",
              optionIds: [],
              text: "跳过",
            },
            {
              questionId: "q2",
              optionIds: [],
              text: "Must be lightweight",
            },
          ],
        },
      }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("submits status: skipped when all questions are skipped in multiple questions", async () => {
    const api = setupApi();
    const onSaved = vi.fn();

    render(<ClarificationComposer request={multiQuestions} ready onSaved={onSaved} />);

    // Q1: click "跳过此题"
    fireEvent.click(screen.getByRole("button", { name: "跳过此题" }));

    // On Q2: click "跳过此题"
    fireEvent.click(screen.getByRole("button", { name: "跳过此题" }));

    // All questions skipped -> submit
    fireEvent.click(screen.getByRole("button", { name: /提交回答/ }));

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-multi",
        response: {
          status: "skipped",
          answers: [],
        },
      }),
    );
    expect(onSaved).toHaveBeenCalled();
  });

  it("supports unskipping and re-answering a previously skipped question", async () => {
    const api = setupApi();
    const onSaved = vi.fn();

    render(<ClarificationComposer request={multiQuestions} ready onSaved={onSaved} />);

    // Q1: click "跳过此题" -> moves to Q2
    fireEvent.click(screen.getByRole("button", { name: "跳过此题" }));
    expect(screen.getByText("Any additional constraints?")).toBeInTheDocument();

    // Navigate back to Q1
    fireEvent.click(screen.getByRole("button", { name: "上一个" }));
    expect(screen.getByText("Choose target platforms")).toBeInTheDocument();
    expect(screen.getByText("已跳过")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消跳过" })).toBeInTheDocument();

    // Click "macOS" option to unskip and answer
    fireEvent.click(screen.getByText("macOS"));
    expect(screen.queryByText("已跳过")).toBeNull();
    expect(screen.getByRole("button", { name: "跳过此题" })).toBeInTheDocument();

    // Navigate to Q2 and answer
    fireEvent.click(screen.getByRole("button", { name: "下一个" }));
    const textarea = screen.getByPlaceholderText("请输入你的回答…");
    fireEvent.change(textarea, { target: { value: "Must be lightweight" } });

    // Submit
    fireEvent.click(screen.getByRole("button", { name: /提交回答/ }));

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-multi",
        response: {
          status: "answered",
          answers: [
            {
              questionId: "q1",
              optionIds: ["mac"],
              text: "",
            },
            {
              questionId: "q2",
              optionIds: [],
              text: "Must be lightweight",
            },
          ],
        },
      }),
    );
  });

  it("blocks submit and shows error if some question is unanswered", async () => {
    const api = setupApi();
    render(<ClarificationComposer request={multiQuestions} ready onSaved={vi.fn()} />);

    // Select Q1
    fireEvent.click(screen.getByText("macOS"));

    // Do NOT answer Q2, try to submit
    const submitBtn = screen.getByRole("button", { name: /提交回答/ });
    expect(submitBtn).toBeDisabled();

    // Force click to test validation branch if disabled is bypassed
    fireEvent.click(submitBtn);
    expect(api.answerUserInput).not.toHaveBeenCalled();
  });

  it("supports selecting '自定义' with required custom text on single select question", async () => {
    const api = setupApi();
    render(<ClarificationComposer request={singleQuestion} ready onSaved={vi.fn()} />);

    // Click "自定义" option
    fireEvent.click(screen.getByText("自定义"));

    // Submit should be disabled because custom text is required and currently empty
    const submitBtn = screen.getByRole("button", { name: /提交回答/ });
    expect(submitBtn).toBeDisabled();

    // Type custom text
    const textarea = screen.getByPlaceholderText(/请输入自定义内容（必填）/);
    fireEvent.change(textarea, { target: { value: "Prefer async kernel" } });

    // Submit is now enabled
    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-single",
        response: {
          status: "answered",
          answers: [
            {
              questionId: "scope",
              optionIds: [],
              text: "Prefer async kernel",
            },
          ],
        },
      }),
    );
  });

  it("supports multi_select with both predefined options and custom text", async () => {
    const api = setupApi();
    render(<ClarificationComposer request={multiQuestions} ready onSaved={vi.fn()} />);

    // Select "macOS"
    fireEvent.click(screen.getByText("macOS"));

    // Also select "自定义"
    fireEvent.click(screen.getByText("自定义"));

    // Submit is disabled because custom text is empty
    const submitBtn = screen.getByRole("button", { name: /提交回答/ });
    expect(submitBtn).toBeDisabled();

    // Type custom text
    const customInput = screen.getByPlaceholderText(/请输入自定义内容（必填）/);
    fireEvent.change(customInput, { target: { value: "Linux container" } });

    // Switch to Q2 and fill it
    fireEvent.click(screen.getByRole("button", { name: "下一个" }));
    const q2Text = screen.getByPlaceholderText("请输入你的回答…");
    fireEvent.change(q2Text, { target: { value: "No extra constraints" } });

    // Submit
    fireEvent.click(screen.getByRole("button", { name: /提交回答/ }));

    await waitFor(() =>
      expect(api.answerUserInput).toHaveBeenCalledWith({
        requestId: "req-multi",
        response: {
          status: "answered",
          answers: [
            {
              questionId: "q1",
              optionIds: ["mac"],
              text: "Linux container",
            },
            {
              questionId: "q2",
              optionIds: [],
              text: "No extra constraints",
            },
          ],
        },
      }),
    );
  });
});
