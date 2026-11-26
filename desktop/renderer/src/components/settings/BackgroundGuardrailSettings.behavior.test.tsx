import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { GuardrailsSaveRequest } from "../../../../shared/guardrails.js";
import { BackgroundGuardrailSettings } from "./BackgroundGuardrailSettings.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const initial = {
  revision: 0,
  policy: {
    maxTokensPerCycle: null,
    maxCostCreditsPerCycle: null,
    maxIdleRounds: 3,
  },
};

describe("Background guardrail settings", () => {
  let readGuardrails: ReturnType<typeof vi.fn>;
  let saveGuardrails: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    readGuardrails = vi.fn().mockResolvedValue(structuredClone(initial));
    saveGuardrails = vi.fn().mockImplementation(async (value: GuardrailsSaveRequest) => ({
      revision: value.expectedRevision + 1,
      policy: value.policy,
    }));
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: { readGuardrails, saveGuardrails },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("loads initial policy from runtime settings and saves updated limits", async () => {
    const user = userEvent.setup();
    const toast = vi.fn();
    render(<BackgroundGuardrailSettings onShowToast={toast} />);

    expect(screen.getByRole("heading", { name: "后台执行护栏" })).toBeInTheDocument();
    const tokensInput = await screen.findByLabelText("单轮 Token 上限");
    const costInput = screen.getByLabelText("单轮 Credits 上限");
    const idleInput = screen.getByLabelText("连续无进展轮次上限");

    await waitFor(() => expect(tokensInput).toBeEnabled());
    expect(tokensInput).toHaveValue("");
    expect(costInput).toHaveValue("");
    expect(idleInput).toHaveValue("3");

    await user.type(tokensInput, "50000");
    await user.type(costInput, "12.5");
    await user.clear(idleInput);
    await user.type(idleInput, "5");
    await user.click(screen.getByRole("button", { name: "保存护栏" }));

    await waitFor(() =>
      expect(saveGuardrails).toHaveBeenCalledWith({
        expectedRevision: 0,
        policy: {
          maxTokensPerCycle: 50000,
          maxCostCreditsPerCycle: 12.5,
          maxIdleRounds: 5,
        },
      }),
    );
    expect(toast).toHaveBeenCalledWith("后台执行护栏已保存", "success");
  });

  it("serializes blank fields as null", async () => {
    const user = userEvent.setup();
    readGuardrails.mockResolvedValue({
      revision: 2,
      policy: {
        maxTokensPerCycle: 10000,
        maxCostCreditsPerCycle: 4.5,
        maxIdleRounds: 3,
      },
    });
    render(<BackgroundGuardrailSettings />);

    const tokensInput = await screen.findByLabelText("单轮 Token 上限");
    const costInput = screen.getByLabelText("单轮 Credits 上限");
    const idleInput = screen.getByLabelText("连续无进展轮次上限");
    await waitFor(() => expect(tokensInput).toHaveValue("10000"));

    await user.clear(tokensInput);
    await user.clear(costInput);
    await user.clear(idleInput);
    await user.click(screen.getByRole("button", { name: "保存护栏" }));

    await waitFor(() =>
      expect(saveGuardrails).toHaveBeenCalledWith({
        expectedRevision: 2,
        policy: {
          maxTokensPerCycle: null,
          maxCostCreditsPerCycle: null,
          maxIdleRounds: null,
        },
      }),
    );
  });

  it("rejects out-of-range inputs before invoking saveGuardrails", async () => {
    const user = userEvent.setup();
    const toast = vi.fn();
    render(<BackgroundGuardrailSettings onShowToast={toast} />);

    const tokensInput = await screen.findByLabelText("单轮 Token 上限");
    const costInput = screen.getByLabelText("单轮 Credits 上限");
    const idleInput = screen.getByLabelText("连续无进展轮次上限");
    await waitFor(() => expect(tokensInput).toBeEnabled());

    await user.type(tokensInput, "500");
    await user.click(screen.getByRole("button", { name: "保存护栏" }));
    expect(saveGuardrails).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("单轮 Token 上限范围为");

    await user.clear(tokensInput);
    await user.type(costInput, "0");
    await user.click(screen.getByRole("button", { name: "保存护栏" }));
    expect(saveGuardrails).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("单轮 Credits 上限需要大于 0");

    await user.clear(costInput);
    await user.clear(idleInput);
    await user.type(idleInput, "0");
    await user.click(screen.getByRole("button", { name: "保存护栏" }));
    expect(saveGuardrails).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("连续无进展轮次上限范围为");
  });

  it("renders nothing when readGuardrails bridge is unavailable", () => {
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: {} });
    const { container } = render(<BackgroundGuardrailSettings />);
    expect(container).toBeEmptyDOMElement();
  });
});
