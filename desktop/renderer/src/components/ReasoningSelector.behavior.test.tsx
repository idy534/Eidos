import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReasoningSelector } from "./ReasoningSelector.js";

describe("ReasoningSelector", () => {
  it("shows None and Thinking for toggle-only models and reports the selected value", () => {
    const onChange = vi.fn();
    const { container } = render(
      <ReasoningSelector
        modelName="MiniMax M3"
        reasoning={{ defaultSelection: "thinking", selections: ["none", "thinking"] }}
        selection="thinking"
        disabled={false}
        onChange={onChange}
      />,
    );

    fireEvent.click(container.querySelector("summary")!);

    const slider = screen.getByRole("slider", { name: "MiniMax M3 思考强度" });
    expect(slider).toHaveAttribute("max", "1");
    expect(slider).toHaveValue("1");
    expect(screen.getByText("None")).toBeInTheDocument();
    expect(slider).toHaveAttribute("aria-valuetext", "Thinking");

    fireEvent.change(slider, { target: { value: "0" } });
    expect(onChange).toHaveBeenCalledWith("none");
  });

  it("maps slider positions to unique supported levels", () => {
    const onChange = vi.fn();
    const { container } = render(
      <ReasoningSelector
        modelName="DeepSeek-V4 Flash"
        reasoning={{ defaultSelection: "high", selections: ["low", "high", "max", "high"] }}
        selection="high"
        disabled={false}
        onChange={onChange}
      />,
    );

    fireEvent.click(container.querySelector("summary")!);

    const slider = screen.getByRole("slider", { name: "DeepSeek-V4 Flash 思考强度" });
    expect(slider).toHaveAttribute("max", "2");
    expect(slider).toHaveValue("1");
    fireEvent.change(slider, { target: { value: "2" } });
    expect(onChange).toHaveBeenCalledWith("max");
  });
});
