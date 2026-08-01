import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { EidosRuntimeAPI, ModelListResult } from "../contracts.js";
import { useModelController } from "./useModelController.js";

const list: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [{
    id: "deepseek-v4-flash", name: "DeepSeek-V4 Flash", vendor: "DeepSeek",
    provider: "deepseek", url: "https://api.deepseek.com/chat/completions",
    supportsToolCall: true, supportsImages: false, supportsReasoning: true,
    reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
  }],
};
const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useModelController", () => {
  afterEach(() => {
    vi.restoreAllMocks();
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
