import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import deepseekLogo from "../assets/providers/deepseek.svg";
import minimaxLogo from "../assets/providers/minimax.svg";
import type { ModelOption } from "../contracts.js";
import { ModelSelector } from "./ModelSelector.js";

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

describe("ModelSelector", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });
  it("shows provider logos and names in the current model and model list", () => {
    const onModelChange = vi.fn();
    const { baseElement: container } = render(
      <ModelSelector
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
    expect(container.querySelector(".model-selector__model-provider")).not.toBeInTheDocument();
    expect(Array.from(container.querySelectorAll("img.provider-logo")).map((logo) => logo.getAttribute("src"))).toContain(minimaxLogo);
    fireEvent.click(minimaxChoice);
    expect(onModelChange).toHaveBeenCalledWith("MiniMax-M3");
  });

  it("maps reasoning slider positions to the supported values", () => {
    const onChange = vi.fn();
    render(
      <ModelSelector
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
      <ModelSelector
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

  it("smoothly transitions slider position to the target when released between steps", () => {
    let rafCallback: FrameRequestCallback | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafCallback = cb;
      return 101;
    });

    const onChange = vi.fn();
    render(
      <ModelSelector
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

    fireEvent.pointerUp(slider, { pointerId: 1 });
    expect(onChange).toHaveBeenCalledWith("low");
    expect(rafCallback).toBeDefined();

    // Advance animation partially
    const stepCb1 = rafCallback;
    act(() => {
      stepCb1!(performance.now() + 100);
    });
    const midVal = Number((slider as HTMLInputElement).value);
    expect(midVal).toBeLessThan(1.4);
    expect(midVal).toBeGreaterThan(1.0);

    // Complete animation on next frame
    const stepCb2 = rafCallback;
    act(() => {
      stepCb2!(performance.now() + 400);
    });
    expect(slider).toHaveValue("1");
  });

  it("cancels transition animation immediately when interrupted by pointerDown", () => {
    let rafCallback: FrameRequestCallback | undefined;
    const cancelSpy = vi.spyOn(window, "cancelAnimationFrame");
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      rafCallback = cb;
      return 202;
    });

    render(
      <ModelSelector
        models={models}
        selectedModelId="deepseek-flash"
        selection="high"
        disabled={false}
        onModelChange={vi.fn()}
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /DeepSeek Flash，深度求索/ }));
    const slider = screen.getByRole("slider", { name: "DeepSeek Flash 思考强度" });
    fireEvent.pointerDown(slider, { pointerId: 1 });
    fireEvent.change(slider, { target: { value: "1.4" } });
    fireEvent.pointerUp(slider, { pointerId: 1 });

    // Step partially
    act(() => {
      rafCallback!(performance.now() + 100);
    });
    const midVal = Number((slider as HTMLInputElement).value);
    expect(midVal).toBeLessThan(1.4);
    expect(midVal).toBeGreaterThan(1.0);

    // User interrupts by pressing pointer down again
    fireEvent.pointerDown(slider, { pointerId: 1 });
    expect(cancelSpy).toHaveBeenCalledWith(202);
    expect(Number((slider as HTMLInputElement).value)).toBeCloseTo(midVal, 2);
  });

  it("snaps immediately without animating when prefers-reduced-motion is true", () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    const rafSpy = vi.spyOn(window, "requestAnimationFrame");
    render(
      <ModelSelector
        models={models}
        selectedModelId="deepseek-flash"
        selection="high"
        disabled={false}
        onModelChange={vi.fn()}
        onChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /DeepSeek Flash，深度求索/ }));
    const slider = screen.getByRole("slider", { name: "DeepSeek Flash 思考强度" });
    fireEvent.pointerDown(slider, { pointerId: 1 });
    fireEvent.change(slider, { target: { value: "1.4" } });
    fireEvent.pointerUp(slider, { pointerId: 1 });

    expect(slider).toHaveValue("1");
    expect(rafSpy).not.toHaveBeenCalled();

    window.matchMedia = originalMatchMedia;
  });

  it("renders its menu outside forms and closes on Escape or disabled state", () => {
    const props = { models, selectedModelId: models[0]!.id, selection: "high" as const, disabled: false, onModelChange: vi.fn(), onChange: vi.fn() };
    const { rerender } = render(<form><ModelSelector {...props} /></form>);
    const trigger = screen.getByRole("button", { name: /DeepSeek Flash.*思考强度/ });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog").parentElement).toBe(document.body);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    rerender(<form><ModelSelector {...props} disabled /></form>);
    expect(trigger).toBeDisabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps missing models visible and uses defaults without changing stored values", () => {
    const onChange = vi.fn();
    const props = { models, selectedModelId: "deleted", selection: undefined, disabled: false, onModelChange: vi.fn(), onChange };
    const { rerender } = render(<ModelSelector {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /已保存的模型不可用/ }));
    expect(screen.getAllByRole("radio")).toHaveLength(models.length);
    fireEvent.keyDown(document, { key: "Escape" });
    rerender(<ModelSelector {...props} selectedModelId="deepseek-flash" />);
    fireEvent.click(screen.getByRole("button", { name: /思考强度 高/ }));
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuetext", "高");
    expect(onChange).not.toHaveBeenCalled();
    rerender(<ModelSelector {...props} selectedModelId="deepseek-flash" selection="medium" />);
    expect(screen.getByRole("button", { name: /思考配置已变更/ })).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole("slider"), { pointerId: 1 });
    fireEvent.pointerUp(screen.getByRole("slider"), { pointerId: 1 });
    expect(onChange).toHaveBeenCalledWith("high");
  });

  it("shows custom metadata and leaves undeclared reasoning to the provider", () => {
    const custom: ModelOption = { ...models[0]!, id: "custom-config", modelId: "MiniMax-M3", name: "公司模型", vendor: "公司网关", provider: "custom",
      supportsImages: false, supportsReasoning: false, reasoning: null, contextWindowTokens: 64000,
      custom: { providerName: "公司网关", baseUrl: "https://gateway.invalid/v1", name: "公司模型", contextWindowTokens: 64000, maxOutputTokens: 4096, reasoningControl: "provider_default" } };
    render(<ModelSelector models={[custom]} selectedModelId={custom.id} selection={undefined} disabled={false} onModelChange={vi.fn()} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "公司模型，公司网关，选择模型" }));
    const choice = screen.getByRole("radio", { name: "公司模型，公司网关" });
    fireEvent.mouseEnter(choice.closest("label")!);
    const info = screen.getByRole("tooltip");
    expect(info).toHaveTextContent("64K");
    expect(info).toHaveTextContent("由服务商决定");
    expect(info).not.toHaveTextContent("不支持推理");
    expect(info).not.toHaveTextContent("视频");
  });

});
