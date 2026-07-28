import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useModelController } from "./useModelController.js";
import type { EidosRuntimeAPI, ModelListResult, ModelStatus } from "../contracts.js";

const mockStatus: ModelStatus = {
  provider: "deepseek",
  model: "deepseek-v4-flash",
  configured: true,
};

const mockUnconfiguredStatus: ModelStatus = {
  provider: "deepseek",
  model: "deepseek-v4-flash",
  configured: false,
};

const mockList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [
    {
      id: "deepseek-v4-flash",
      provider: "deepseek",
      displayName: "DeepSeek V4 Flash",
      configured: true,
      selectable: true,
    },
    {
      id: "deepseek-v4-pro",
      provider: "deepseek",
      displayName: "DeepSeek V4 Pro",
      configured: true,
      selectable: true,
    },
  ],
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useModelController real behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupMockRuntime(overrides: Partial<EidosRuntimeAPI> = {}) {
    const api: Partial<EidosRuntimeAPI> = {
      getModelStatus: vi.fn().mockResolvedValue(mockStatus),
      listModels: vi.fn().mockResolvedValue(mockList),
      configureModel: vi.fn().mockResolvedValue(mockStatus),
      ...overrides,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  it("1. load() immediately enters loading state", async () => {
    let resolveStatus!: (val: ModelStatus) => void;
    setupMockRuntime({
      getModelStatus: vi.fn().mockImplementation(() => new Promise((r) => { resolveStatus = r; })),
    });

    const { result } = renderHook(() => useModelController());
    expect(result.current[0].loading).toBe(false);

    let loadPromise: Promise<void>;
    act(() => {
      loadPromise = result.current[1].load();
    });

    expect(result.current[0].loading).toBe(true);

    await act(async () => {
      resolveStatus(mockStatus);
      await loadPromise;
    });

    expect(result.current[0].loading).toBe(false);
  });

  it("2. Loading finishes after both status and list requests resolve", async () => {
    setupMockRuntime();
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].loading).toBe(false);
    expect(result.current[0].status).toEqual(mockStatus);
    expect(result.current[0].list).toEqual(mockList);
  });

  it("3. Configured state is restored", async () => {
    setupMockRuntime({ getModelStatus: vi.fn().mockResolvedValue(mockStatus) });
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].status?.configured).toBe(true);
  });

  it("4. Unconfigured state is restored", async () => {
    setupMockRuntime({ getModelStatus: vi.fn().mockResolvedValue(mockUnconfiguredStatus) });
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].status?.configured).toBe(false);
  });

  it("5. Session model has selection priority", async () => {
    setupMockRuntime();
    const { result } = renderHook(() => useModelController());

    act(() => {
      result.current[1].initialize(mockStatus, mockList, "deepseek-v4-pro");
    });

    expect(result.current[0].selectedModelId).toBe("deepseek-v4-pro");
  });

  it("6. Current valid selection has second priority", async () => {
    setupMockRuntime();
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    act(() => {
      result.current[1].selectModel("deepseek-v4-pro");
    });

    expect(result.current[0].selectedModelId).toBe("deepseek-v4-pro");

    // Load again without session model
    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].selectedModelId).toBe("deepseek-v4-pro");
  });

  it("7. Runtime default is used when needed", async () => {
    setupMockRuntime();
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].selectedModelId).toBe("deepseek-v4-flash");
  });

  it("8. Non-selectable models are ignored", async () => {
    const listWithDisabled: ModelListResult = {
      defaultModelId: "deepseek-v4-pro",
      models: [
        { id: "deepseek-v4-pro", provider: "deepseek", displayName: "Pro Disabled", configured: false, selectable: false },
        { id: "deepseek-v4-flash", provider: "deepseek", displayName: "Flash", configured: true, selectable: true },
      ],
    };
    setupMockRuntime({ listModels: vi.fn().mockResolvedValue(listWithDisabled) });

    const { result } = renderHook(() => useModelController());
    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].selectedModelId).toBe("deepseek-v4-flash");
  });

  it("9. Load failure preserves previous valid state", async () => {
    const api = setupMockRuntime();
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].selectedModelId).toBe("deepseek-v4-flash");

    // Make next load fail
    api.getModelStatus = vi.fn().mockRejectedValue(new Error("Network Error"));

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].selectedModelId).toBe("deepseek-v4-flash");
    expect(result.current[0].status).toEqual(mockStatus);
  });

  it("10. Load failure creates a Model-local error", async () => {
    setupMockRuntime({ getModelStatus: vi.fn().mockRejectedValue(new Error("Backend Offline")) });
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
  });

  it("11. configure() enters configuring state", async () => {
    let resolveConfig!: (val: ModelStatus) => void;
    setupMockRuntime({
      configureModel: vi.fn().mockImplementation(() => new Promise((r) => { resolveConfig = r; })),
    });

    const { result } = renderHook(() => useModelController());

    let configPromise: Promise<void>;
    act(() => {
      configPromise = result.current[1].configure("key-123");
    });

    expect(result.current[0].configuring).toBe(true);

    await act(async () => {
      resolveConfig(mockStatus);
      await configPromise;
    });

    expect(result.current[0].configuring).toBe(false);
  });

  it("12. Two rapid configure calls invoke IPC once", async () => {
    const configureSpy = vi.fn().mockResolvedValue(mockStatus);
    setupMockRuntime({ configureModel: configureSpy });

    const { result } = renderHook(() => useModelController());

    await act(async () => {
      const p1 = result.current[1].configure("key-1");
      const p2 = result.current[1].configure("key-2");
      await Promise.all([p1, p2]);
    });

    expect(configureSpy).toHaveBeenCalledTimes(1);
    expect(configureSpy).toHaveBeenCalledWith("key-1");
  });

  it("13. Configure success reloads model list", async () => {
    const listSpy = vi.fn().mockResolvedValue(mockList);
    setupMockRuntime({ listModels: listSpy });

    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].configure("key-123");
    });

    expect(listSpy).toHaveBeenCalled();
    expect(result.current[0].status).toEqual(mockStatus);
  });

  it("14. Configure failure preserves previous valid state", async () => {
    const api = setupMockRuntime();
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    api.configureModel = vi.fn().mockRejectedValue(new Error("Invalid API key"));

    await act(async () => {
      const res = await result.current[1].configure("bad-key");
      expect(res).toBe(false);
    });

    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
    expect(result.current[0].status).toEqual(mockStatus);
  });

  it("15. Invalid selection is rejected", async () => {
    setupMockRuntime();
    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    act(() => {
      result.current[1].selectModel("non-existent-model" as any);
    });

    expect(result.current[0].error).toBe("Model non-existent-model is not selectable");
    expect(result.current[0].selectedModelId).toBe("deepseek-v4-flash");
  });

  it("16. No selectable model leaves submission unavailable", async () => {
    const emptyList: ModelListResult = {
      defaultModelId: "disabled",
      models: [
        { id: "disabled" as any, provider: "deepseek", displayName: "Disabled", configured: false, selectable: false },
      ],
    };
    setupMockRuntime({ listModels: vi.fn().mockResolvedValue(emptyList) });

    const { result } = renderHook(() => useModelController());

    await act(async () => {
      await result.current[1].load();
    });

    expect(result.current[0].selectedModelId).toBeUndefined();
    expect(result.current[0].error).toBe("No selectable model available from Runtime");
  });
});
