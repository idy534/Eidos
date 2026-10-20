import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useQueuedMessages } from "./useQueuedMessages.js";

describe("queued message reads", () => {
  it("refreshes a superseded failure before displaying it and keeps current failures visible", async () => {
    let notify: ((notice: unknown) => void) | undefined;
    let fail!: (reason: Error) => void;
    const error = new Error("EIDOS_RUNTIME_ERROR:RESOURCE_NOT_FOUND");
    const list = vi.fn().mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }))
      .mockResolvedValueOnce({ items: [] }).mockRejectedValue(error);
    window.eidosRuntime = { listQueuedRuns: list, onNotification: (callback: typeof notify) => { notify = callback; return () => {}; } } as unknown as typeof window.eidosRuntime;
    const { result } = renderHook(() => useQueuedMessages("s", true));
    act(() => notify?.({ method: "run/queueChanged", params: { change: { sessionId: "s" } } }));
    await act(async () => fail(error));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(result.current.error).toBeUndefined();
    await act(async () => result.current.refresh());
    expect(result.current.error).toBe("系统暂时无法读取当前会话的待发送消息，请刷新后重试。");
  });
  it("does not let an obsolete Session response overwrite the selected queue", async () => {
    let finish: ((value: { items: [] }) => void) | undefined;
    window.eidosRuntime = { listQueuedRuns: vi.fn((request) => request.sessionId === "a"
      ? new Promise((resolve) => { finish = resolve; })
      : Promise.resolve({ items: [{ sessionId: "b", runId: "b-run", userInput: "B", references: [] }] })) } as unknown as typeof window.eidosRuntime;
    const { result, rerender } = renderHook(({ session }) => useQueuedMessages(session, true), { initialProps: { session: "a" } });
    rerender({ session: "b" });
    await waitFor(() => expect(result.current.items[0]?.runId).toBe("b-run"));
    await act(async () => finish?.({ items: [] }));
    expect(result.current.items[0]?.runId).toBe("b-run");
  });

  it("loads later pages and refreshes when a change arrives during a read", async () => {
    let notify: ((notice: unknown) => void) | undefined;
    let finish: ((value: unknown) => void) | undefined;
    const first = { sessionId: "s", runId: "first", userInput: "first", references: [] };
    const list = vi.fn().mockResolvedValueOnce({ items: [first], nextSequence: 10 })
      .mockResolvedValueOnce({ items: [{ ...first, runId: "second" }] })
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
      .mockResolvedValue({ items: [{ ...first, userInput: "fresh" }] });
    window.eidosRuntime = { listQueuedRuns: list, onNotification: (callback: typeof notify) => { notify = callback; return () => {}; } } as unknown as typeof window.eidosRuntime;
    const { result } = renderHook(() => useQueuedMessages("s", true));
    await waitFor(() => expect(result.current.nextSequence).toBe(10));
    await act(async () => result.current.refresh(10));
    expect(result.current.items.map((item) => item.runId)).toEqual(["first", "second"]);
    act(() => { void result.current.refresh(); });
    act(() => notify?.({ method: "run/queueChanged", params: { change: { kind: "deleted", sessionId: "s", runId: "second" } } }));
    await act(async () => finish?.({ items: [first] }));
    await waitFor(() => expect(result.current.items[0]?.userInput).toBe("fresh"));
  });
});
