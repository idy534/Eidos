import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import deepseekLogo from "../assets/providers/deepseek.svg";
import minimaxLogo from "../assets/providers/minimax.svg";
import type { ModelOption } from "../contracts.js";
import { ReasoningSelector } from "./ReasoningSelector.js";

const models: ModelOption[] = [
  {
    id: "deepseek-flash",
    name: "DeepSeek Flash",
    vendor: "DeepSeek",
    provider: "deepseek",
    url: "https://api.deepseek.com/chat/completions",
    supportsToolCall: true,
    supportsImages: false,
    supportsReasoning: true,
    reasoning: { defaultSelection: "high", selections: ["none", "low", "high", "max"] },
  },
  {
    id: "MiniMax-M3",
    name: "MiniMax M3",
    vendor: "MiniMax",
    provider: "minimax",
    url: "https://api.minimaxi.com/v1/chat/completions",
    supportsToolCall: true,
    supportsImages: false,
    supportsReasoning: true,
    reasoning: { defaultSelection: "thinking", selections: ["none", "thinking"] },
  },
];

describe("ReasoningSelector", () => {
  it("shows provider logos and names in the current model and model list", () => {
    const onModelChange = vi.fn();
    const { container } = render(
      <ReasoningSelector
        models={models}
        selectedModelId="deepseek-flash"
        selection="high"
        disabled={false}
        onModelChange={onModelChange}
        onChange={vi.fn()}
      />,
    );

    const trigger = screen.getByRole("button", { name: /DeepSeek Flash，深度求索/ });
    expect(trigger.querySelector("img")).toHaveAttribute("src", deepseekLogo);
    expect(trigger).not.toHaveTextContent("深度求索");
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: /切换模型，当前为 DeepSeek Flash/ }));

    const minimaxChoice = screen.getByRole("radio", { name: "MiniMax M3，MiniMax" });
    expect(minimaxChoice.closest("label")).toHaveTextContent("MiniMax M3");
    expect(container.querySelector(".reasoning-selector__model-provider")).not.toBeInTheDocument();
    expect(Array.from(container.querySelectorAll("img.provider-logo")).map((logo) => logo.getAttribute("src"))).toContain(minimaxLogo);
    fireEvent.click(minimaxChoice);
    expect(onModelChange).toHaveBeenCalledWith("MiniMax-M3");
  });

  it("maps reasoning slider positions to the supported values", () => {
    const onChange = vi.fn();
    render(
      <ReasoningSelector
        models={models}
        selectedModelId="MiniMax-M3"
        selection="thinking"
        disabled={false}
        onModelChange={vi.fn()}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /MiniMax M3，MiniMax/ }));
    const slider = screen.getByRole("slider", { name: "MiniMax M3 思考强度" });
    expect(slider).toHaveAttribute("max", "1");
    expect(slider).toHaveValue("1");
    fireEvent.change(slider, { target: { value: "0" } });
    expect(onChange).toHaveBeenCalledWith("none");
  });

  it("keeps the slider continuous while dragging and snaps on release", () => {
    const onChange = vi.fn();
    render(
      <ReasoningSelector
        models={models}
        selectedModelId="deepseek-flash"
        selection="high"
        disabled={false}
        onModelChange={vi.fn()}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /DeepSeek Flash，深度求索/ }));
    const slider = screen.getByRole("slider", { name: "DeepSeek Flash 思考强度" });
    fireEvent.pointerDown(slider, { pointerId: 1 });
    fireEvent.change(slider, { target: { value: "1.4" } });

    expect(slider).toHaveValue("1.4");
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.pointerUp(slider, { pointerId: 1 });
    expect(onChange).toHaveBeenCalledWith("low");
  });
});
