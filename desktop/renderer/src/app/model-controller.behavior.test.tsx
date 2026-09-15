import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { EidosRuntimeAPI, ModelListResult } from "../contracts.js";
import { useModelController } from "./useModelController.js";

const list: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [{
    id: "deepseek-v4-flash", name: "DeepSeek-V4 Flash", vendor: "DeepSeek",
    provider: "deepseek", url: "https://api.deepseek.com/chat/completions",
    supportsToolCall: true, supportsImages: false, supportsReasoning: true,
    reasoning: { defaultSelection: "high", selections: ["high", "max"] },
  }, {
    id: "kimi-k3", name: "Kimi K3", vendor: "Kimi", provider: "kimi",
    url: "https://api.moonshot.cn/v1/chat/completions",
    supportsToolCall: true, supportsImages: false, supportsReasoning: true,
    reasoning: { defaultSelection: "max", selections: ["low", "high", "max"] },
  }],
};
const REASONING_OVERRIDES_KEY = "eidos.modelReasoningOverrides.v1";
const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useModelController", () => {
  beforeEach(() => {
    window.localStorage.removeItem(REASONING_OVERRIDES_KEY);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.removeItem(REASONING_OVERRIDES_KEY);
    if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("loads only model/list and restores the first configured model", async () => {
    const listModels = vi.fn().mockResolvedValue(list);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModels,
    } as EidosRuntimeAPI;
    const { result } = renderHook(() => useModelController());

    await act(async () => result.current[1].load());

    expect(listModels).toHaveBeenCalledTimes(1);
    expect(result.current[0].list).toEqual(list);
    expect(result.current[0].selectedModelId).toBe("deepseek-v4-flash");
  });

  it("uses each model default and persists only valid per-model overrides", async () => {
    const listModels = vi.fn().mockResolvedValue(list);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModels,
    } as EidosRuntimeAPI;
    const first = renderHook(() => useModelController());

    await act(async () => first.result.current[1].load());
    expect(first.result.current[0].reasoningSelection).toBe("high");

    act(() => first.result.current[1].setReasoningSelection("deepseek-v4-flash", "max"));
    expect(first.result.current[0].reasoningSelection).toBe("max");
    act(() => first.result.current[1].selectModel("kimi-k3"));
    expect(first.result.current[0].reasoningSelection).toBe("max");
    act(() => first.result.current[1].setReasoningSelection("kimi-k3", "high"));
    expect(JSON.parse(window.localStorage.getItem(REASONING_OVERRIDES_KEY) ?? "{}"))
      .toEqual({ "deepseek-v4-flash": "max", "kimi-k3": "high" });
    first.unmount();

    const restored = renderHook(() => useModelController());
    await act(async () => restored.result.current[1].load());
    expect(restored.result.current[0].reasoningSelection).toBe("max");
    act(() => restored.result.current[1].selectModel("kimi-k3"));
    expect(restored.result.current[0].reasoningSelection).toBe("high");

    act(() => restored.result.current[1].setReasoningSelection("deepseek-v4-flash", "high"));
    expect(JSON.parse(window.localStorage.getItem(REASONING_OVERRIDES_KEY) ?? "{}"))
      .toEqual({ "kimi-k3": "high" });
    restored.unmount();
  });

  it("falls back to the configured default when a stored override is no longer supported", async () => {
    window.localStorage.setItem(REASONING_OVERRIDES_KEY, JSON.stringify({ "deepseek-v4-flash": "low" }));
    const listModels = vi.fn().mockResolvedValue(list);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModels,
    } as EidosRuntimeAPI;
    const { result } = renderHook(() => useModelController());

    await act(async () => result.current[1].load());

    expect(result.current[0].reasoningSelection).toBe("high");
  });

  it("refresh after deletion falls back while a failed refresh preserves state", async () => {
    const listModels = vi.fn().mockResolvedValueOnce(list).mockResolvedValueOnce({ models: [], defaultModelId: null });
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      listModels,
    } as EidosRuntimeAPI;
    const { result } = renderHook(() => useModelController());
    await act(async () => result.current[1].load());
    await act(async () => result.current[1].load());
    expect(result.current[0].selectedModelId).toBeUndefined();

    listModels.mockRejectedValueOnce(new Error("offline"));
    await act(async () => result.current[1].load());
    expect(result.current[0].list?.models).toEqual([]);
    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
  });
});
