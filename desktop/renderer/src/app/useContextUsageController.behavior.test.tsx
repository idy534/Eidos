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
  updatedAt: 0,
};

const details = { runId: "run-1", measurement: "provider" as const, activeTokens: usage.activeTokens,
  contextWindowTokens: usage.windowTokens, sections: [{ name: "conversation" as const, tokens: usage.activeTokens }], updatedAt: 0 };
const unavailable = { ...details, measurement: "unavailable" as const, activeTokens: null, sections: [] };

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useContextUsageController", () => {
  it("coalesces streaming notifications and refreshes the current snapshot after an in-flight read", async () => {
    let finish!: (value: typeof details) => void;
    const latest = { ...details, activeTokens: 200_000, sections: [{ name: "conversation" as const, tokens: 200_000 }] };
    const readContextDetails = vi.fn().mockResolvedValueOnce(details)
      .mockImplementationOnce(() => new Promise<typeof details>((resolve) => { finish = resolve; }))
      .mockResolvedValue(latest);
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readContextDetails } });
    const { result } = renderHook(() => useContextUsageController({ ready: true, sessionId: "session-1", modelId: run.modelId, runId: run.id }));
    await act(async () => { await Promise.resolve(); });
    act(() => {
      for (let index = 0; index < 25; index++) result.current[1].handleNotification({ method: "run/updated", params: { sessionId: "session-1", run } });
    });
    expect(readContextDetails).toHaveBeenCalledTimes(2);
    await act(async () => finish(details));
    expect(readContextDetails).toHaveBeenCalledTimes(3);
    expect(result.current[0].usage?.activeTokens).toBe(200_000);
    expect(result.current[0].details).toEqual(latest);
  });
  it("rejects late usage after disabling the selected model and ignores older Run notifications", async () => {
    let resolve!: (value: typeof details) => void;
    const readContextDetails = vi.fn().mockReturnValueOnce(new Promise<typeof details>((done) => { resolve = done; })).mockResolvedValue(unavailable);
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readContextDetails } });
    const { result, rerender } = renderHook((props: { modelId: string | undefined; runId: string | undefined }) => useContextUsageController({ ready: true, sessionId: "session-1", ...props }),
      { initialProps: { modelId: "deepseek-v4-flash", runId: "run-1" } as { modelId: string | undefined; runId: string | undefined } });
    rerender({ modelId: undefined, runId: "run-1" });
    await act(async () => { resolve(details); });
    expect(result.current[0].usage).toBeUndefined();
    rerender({ modelId: "deepseek-v4-flash", runId: "run-2" });
    await act(async () => { await Promise.resolve(); });
    act(() => result.current[1].handleNotification({ method: "run/completed", params: { sessionId: "session-1", run: { ...run, status: "succeeded" } } }));
    expect(readContextDetails).toHaveBeenCalledTimes(2);
    expect(result.current[0].usage).toBeUndefined();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("refreshes provider usage after a Run update and ignores a switched model", async () => {
    const readContextDetails = vi.fn()
      .mockResolvedValueOnce(details)
      .mockResolvedValueOnce({ ...details, activeTokens: 200_000, sections: [{ name: "conversation", tokens: 200_000 }] });
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextDetails,
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
    expect(readContextDetails).toHaveBeenCalledTimes(2);
  });

  it("retains usage for a new Run until its own statistics arrive after compaction", async () => {
    const updatedUsage = { ...usage, activeTokens: 190_000, percentUsed: 73.6 };
    const readContextDetails = vi.fn()
      .mockResolvedValueOnce(details)
      .mockResolvedValueOnce({ ...unavailable, runId: "run-2" })
      .mockResolvedValueOnce({ ...details, runId: "run-2", activeTokens: updatedUsage.activeTokens, sections: [{ name: "conversation", tokens: updatedUsage.activeTokens }] });

    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextDetails,
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

    await act(async () => {
      rerender("run-2");
      await Promise.resolve();
    });
    expect(result.current[0].usage).toEqual(usage);
    expect(result.current[0].details).toEqual(details);

    expect(readContextDetails).toHaveBeenCalledTimes(2);

    await act(async () => {
      result.current[1].handleNotification({
        method: "context/compacted",
        params: {
          sessionId: "session-1",
          runId: "run-2",
          summaryId: "summary-1",
          sourceItemCount: 8,
          phase: "mid_turn",
        },
      });
      await Promise.resolve();
    });
    expect(result.current[0].usage).toEqual(updatedUsage);
    expect(result.current[0].details?.runId).toBe("run-2");
  });

  it("preserves the previous reading during a pending read and repeated unavailable updates", async () => {
    let finish!: (value: typeof unavailable) => void;
    const nextDetails = { ...details, runId: "run-2", activeTokens: 200_000 };
    const readContextDetails = vi.fn().mockResolvedValueOnce(details)
      .mockImplementationOnce(() => new Promise<typeof unavailable>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce({ ...unavailable, runId: "run-2" })
      .mockRejectedValueOnce(new Error("temporary read failure"))
      .mockResolvedValueOnce(nextDetails)
      .mockResolvedValueOnce({ ...unavailable, runId: "run-2" });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readContextDetails } });
    const { result, rerender } = renderHook((runId: string) => useContextUsageController({
      ready: true, sessionId: "session-1", modelId: run.modelId, runId,
    }), { initialProps: "run-1" });
    await act(async () => { await Promise.resolve(); });
    rerender("run-2");
    expect(result.current[0].loading).toBe(true);
    expect(result.current[0].usage).toEqual(usage);
    expect(result.current[0].details).toEqual(details);
    await act(async () => finish({ ...unavailable, runId: "run-2" }));
    for (let index = 0; index < 2; index++) {
      await act(async () => {
        result.current[1].handleNotification({ method: "run/updated", params: { sessionId: "session-1", run: { ...run, id: "run-2" } } });
      });
      expect(result.current[0].usage).toEqual(usage);
      expect(result.current[0].details).toEqual(details);
      expect(result.current[0].loading).toBe(false);
    }
    await act(async () => {
      result.current[1].handleNotification({ method: "run/updated", params: { sessionId: "session-1", run: { ...run, id: "run-2" } } });
    });
    expect(result.current[0].usage?.activeTokens).toBe(200_000);
    expect(result.current[0].details).toEqual(nextDetails);
    // Once this Run has statistics, an unavailable projection must clear them.
    await act(async () => {
      result.current[1].handleNotification({ method: "run/updated", params: { sessionId: "session-1", run: { ...run, id: "run-2" } } });
    });
    expect(result.current[0].usage).toBeUndefined();
  });

  it("ignores a late previous Run refresh while retaining its last confirmed reading", async () => {
    let finish!: (value: typeof details) => void;
    const readContextDetails = vi.fn().mockResolvedValueOnce(details)
      .mockImplementationOnce(() => new Promise<typeof details>((resolve) => { finish = resolve; }))
      .mockResolvedValueOnce({ ...unavailable, runId: "run-2" });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readContextDetails } });
    const { result, rerender } = renderHook((runId: string) => useContextUsageController({
      ready: true, sessionId: "session-1", modelId: run.modelId, runId,
    }), { initialProps: "run-1" });
    await act(async () => { await Promise.resolve(); });
    act(() => result.current[1].handleNotification({ method: "run/updated", params: { sessionId: "session-1", run } }));
    await act(async () => { rerender("run-2"); });
    await act(async () => finish({ ...details, activeTokens: 210_000 }));
    act(() => result.current[1].handleNotification({ method: "run/completed", params: { sessionId: "session-1", run: { ...run, status: "succeeded" } } }));
    expect(result.current[0].usage).toEqual(usage);
    expect(result.current[0].details).toEqual(details);
    expect(readContextDetails).toHaveBeenCalledTimes(3);
  });

  it.each([
    { sessionId: "session-2", modelId: run.modelId, runId: "run-2", ready: true },
    { sessionId: "session-1", modelId: "other-model", runId: "run-2", ready: true },
    { sessionId: "session-1", modelId: run.modelId, runId: undefined, ready: true },
    { sessionId: "session-1", modelId: run.modelId, runId: "run-2", ready: false },
  ])("clears the previous reading when the display scope changes: %j", async (next) => {
    let finish!: (value: typeof details) => void;
    const readContextDetails = vi.fn().mockResolvedValueOnce(details)
      .mockImplementationOnce(() => new Promise<typeof details>((resolve) => { finish = resolve; }))
      .mockResolvedValue(unavailable);
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readContextDetails } });
    const initialProps = { ready: true, sessionId: "session-1", modelId: run.modelId as string, runId: "run-1" as string | undefined };
    const { result, rerender } = renderHook((props: typeof initialProps) => useContextUsageController(props), { initialProps });
    await act(async () => { await Promise.resolve(); });
    rerender({ ...initialProps, runId: "run-2" });
    expect(result.current[0].usage).toEqual(usage);
    await act(async () => { rerender(next); });
    await act(async () => finish({ ...details, runId: "run-2" }));
    expect(result.current[0].usage).toBeUndefined();
    expect(result.current[0].details?.activeTokens ?? null).toBeNull();
  });

  it("clears existing usage when a refresh returns no statistics", async () => {
    const readContextDetails = vi.fn()
      .mockResolvedValueOnce(details)
      .mockResolvedValueOnce(unavailable);
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = {
      readContextDetails,
    } as EidosRuntimeAPI;
    const { result } = renderHook(() => useContextUsageController({
      ready: true,
      sessionId: "session-1",
      modelId: "deepseek-v4-flash",
      runId: "run-1",
    }));
    await act(async () => { await Promise.resolve(); });
    expect(result.current[0].usage).toEqual(usage);

    await act(async () => {
      result.current[1].handleNotification({
        method: "run/updated",
        params: { sessionId: "session-1", run },
      });
      await Promise.resolve();
    });

    expect(result.current[0].usage).toBeUndefined();
    expect(result.current[0].loading).toBe(false);
  });
});
