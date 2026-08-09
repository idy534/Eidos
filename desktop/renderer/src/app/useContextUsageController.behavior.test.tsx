import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EidosRuntimeAPI } from "../contracts.js";
import { useContextUsageController } from "./useContextUsageController.js";

const run = {
  id: "run-1",
  sessionId: "session-1",
  status: "running" as const,
  modelId: "deepseek-v4-flash" as const,
  modelStepCount: 1,
  createdAt: 1,
  updatedAt: 2,
};

const usage = {
  activeTokens: 185_000,
  windowTokens: 258_000,
  percentUsed: 71.7,
  source: "provider" as const,
};

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useContextUsageController", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("refreshes provider usage after a Run update and ignores a switched model", async () => {
    const readContextUsage = vi.fn()
      .mockResolvedValueOnce(usage)
      .mockResolvedValueOnce({ ...usage, activeTokens: 200_000, percentUsed: 77.5 });
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextUsage,
    } as EidosRuntimeAPI;

    const { result, rerender } = renderHook(
      (props: { modelId: "deepseek-v4-flash" | undefined; runId: string | undefined }) =>
        useContextUsageController({
          ready: true,
          sessionId: "session-1",
          modelId: props.modelId,
          runId: props.runId,
        }),
      { initialProps: { modelId: "deepseek-v4-flash", runId: "run-1" } },
    );

    await act(async () => { await Promise.resolve(); });
    expect(result.current[0].usage).toEqual(usage);

    await act(async () => {
      result.current[1].handleNotification({
        method: "run/updated",
        params: { sessionId: "session-1", run },
      });
      await Promise.resolve();
    });
    expect(result.current[0].usage?.activeTokens).toBe(200_000);

    rerender({ modelId: undefined, runId: undefined });
    expect(result.current[0].usage).toBeUndefined();
    expect(readContextUsage).toHaveBeenCalledTimes(2);
  });

  it("clears a new Run until Runtime has a new usage snapshot", async () => {
    const readContextUsage = vi.fn().mockResolvedValueOnce(usage).mockResolvedValueOnce(null);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextUsage,
    } as EidosRuntimeAPI;
    const { result, rerender } = renderHook(
      (runId: string | undefined) => useContextUsageController({
        ready: true,
        sessionId: "session-1",
        modelId: "deepseek-v4-flash",
        runId,
      }),
      { initialProps: "run-1" },
    );

    await act(async () => { await Promise.resolve(); });
    expect(result.current[0].usage).toEqual(usage);
    rerender("run-2");
    expect(result.current[0].usage).toBeUndefined();
    await act(async () => { await Promise.resolve(); });
    expect(result.current[0].usage).toBeUndefined();
  });
});
