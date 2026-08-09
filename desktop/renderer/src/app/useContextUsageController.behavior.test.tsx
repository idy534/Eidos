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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

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

  it("keeps the selected Run when a same-model stale response arrives late", async () => {
    const runA = { ...run, id: "run-a" };
    const runB = { ...run, id: "run-b" };
    const pendingA = deferred<typeof usage>();
    const pendingB = deferred<typeof usage>();
    const readContextUsage = vi.fn((runId: string) => (
      runId === "run-a" ? pendingA.promise : pendingB.promise
    ));
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextUsage,
    } as EidosRuntimeAPI;

    const { result, rerender } = renderHook(
      (props: { runId: string }) => useContextUsageController({
        ready: true,
        sessionId: "session-1",
        modelId: "deepseek-v4-flash",
        runId: props.runId,
      }),
      { initialProps: { runId: runA.id } },
    );

    await act(async () => {
      rerender({ runId: runB.id });
      await Promise.resolve();
    });
    await act(async () => {
      result.current[1].handleNotification({
        method: "run/updated",
        params: { sessionId: "session-1", run: runA },
      });
      await Promise.resolve();
    });
    await act(async () => {
      pendingB.resolve({ ...usage, activeTokens: 200_000 });
      await pendingB.promise;
    });
    await act(async () => {
      pendingA.resolve({ ...usage, activeTokens: 111_000 });
      await pendingA.promise;
    });

    expect(result.current[0].usage?.activeTokens).toBe(200_000);
    expect(readContextUsage).toHaveBeenCalledWith("run-a");
    expect(readContextUsage).toHaveBeenCalledWith("run-b");
  });

  it("rejects a stale response after the session or model changes", async () => {
    const pendingOld = deferred<typeof usage>();
    const pendingNew = deferred<typeof usage>();
    const readContextUsage = vi.fn()
      .mockReturnValueOnce(pendingOld.promise)
      .mockReturnValueOnce(pendingNew.promise);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextUsage,
    } as EidosRuntimeAPI;

    const { result, rerender } = renderHook(
      (props: { sessionId: string; modelId: "deepseek-v4-flash" | "kimi-k2" }) =>
        useContextUsageController({
          ready: true,
          sessionId: props.sessionId,
          modelId: props.modelId,
          runId: "run-a",
        }),
      { initialProps: { sessionId: "session-1", modelId: "deepseek-v4-flash" } },
    );

    await act(async () => {
      rerender({ sessionId: "session-2", modelId: "kimi-k2" });
      pendingOld.resolve({ ...usage, activeTokens: 111_000 });
      await pendingOld.promise;
    });

    expect(result.current[0].usage).toBeUndefined();
  });

  it("does not apply an item refresh after the selected model changes", async () => {
    const initial = deferred<typeof usage>();
    const pendingItem = deferred<typeof usage>();
    const pendingSelected = deferred<typeof usage>();
    const readContextUsage = vi.fn()
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(pendingItem.promise)
      .mockReturnValueOnce(pendingSelected.promise);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextUsage,
    } as EidosRuntimeAPI;

    const { result, rerender } = renderHook(
      (modelId: "deepseek-v4-flash" | "kimi-k2") => useContextUsageController({
        ready: true,
        sessionId: "session-1",
        modelId,
        runId: "run-a",
      }),
      { initialProps: "deepseek-v4-flash" },
    );

    await act(async () => {
      initial.resolve(usage);
      await initial.promise;
    });
    await act(async () => {
      result.current[1].handleNotification({
        method: "item/completed",
        params: { sessionId: "session-1", runId: "run-a" },
      });
      await Promise.resolve();
    });
    await act(async () => {
      rerender("kimi-k2");
      await Promise.resolve();
      pendingSelected.resolve({ ...usage, activeTokens: 220_000 });
      await pendingSelected.promise;
      pendingItem.resolve({ ...usage, activeTokens: 111_000 });
      await pendingItem.promise;
    });

    expect(result.current[0].usage?.activeTokens).toBe(220_000);
  });
});
