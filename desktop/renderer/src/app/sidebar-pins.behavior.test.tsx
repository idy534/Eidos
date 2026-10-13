import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SidebarPinsResponse, SidebarTarget } from "../../../shared/sidebar.js";
import { useSidebarPins } from "./useSidebarPins.js";

const target: SidebarTarget = { kind: "project", id: "11111111-1111-1111-1111-111111111111" };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const list = vi.fn();
const setPinned = vi.fn();
beforeEach(() => {
  list.mockReset().mockResolvedValue({ items: [], throughEventId: 0 });
  setPinned.mockReset();
  window.eidosRuntime = { listSidebarPins: list, setSidebarPinned: setPinned } as unknown as typeof window.eidosRuntime;
});

describe("sidebar pins authoritative state", () => {
  it("does not accept an old mutation response after a newer notification and read", async () => {
    const response = deferred<SidebarPinsResponse>();
    setPinned.mockReturnValue(response.promise);
    const { result } = renderHook(() => useSidebarPins(true));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    act(() => { void result.current.setPinned(target, true); });
    list.mockResolvedValue({ items: [], throughEventId: 12 });
    act(() => { result.current.handleChanged(12); });
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    await act(async () => { response.resolve({ items: [target], throughEventId: 11 }); });
    expect(result.current.pins).toEqual([]);
    expect(result.current.pending.size).toBe(0);
  });

  it("keeps saved pins after a failed write and prevents repeated pending requests", async () => {
    list.mockResolvedValue({ items: [target], throughEventId: 1 });
    const response = deferred<SidebarPinsResponse>();
    setPinned.mockReturnValueOnce(response.promise).mockRejectedValueOnce(new Error("save failed"));
    const { result } = renderHook(() => useSidebarPins(true));
    await waitFor(() => expect(result.current.loaded).toBe(true));
    act(() => {
      void result.current.setPinned(target, false);
      void result.current.setPinned(target, false);
    });
    expect(setPinned).toHaveBeenCalledTimes(1);
    await act(async () => { response.resolve({ items: [], throughEventId: 2 }); });
    await act(async () => { await result.current.setPinned(target, true); });
    expect(result.current.pins).toEqual([]);
    expect(result.current.error).toBeTruthy();
    expect(result.current.pending.size).toBe(0);
  });

  it("rejects a read started before the latest notification", async () => {
    const oldRead = deferred<SidebarPinsResponse>();
    list.mockReturnValueOnce(oldRead.promise).mockResolvedValue({ items: [target], throughEventId: 5 });
    const { result } = renderHook(() => useSidebarPins(true));
    act(() => { result.current.handleChanged(5); });
    await waitFor(() => expect(result.current.pins).toEqual([target]));
    await act(async () => { oldRead.resolve({ items: [], throughEventId: 0 }); });
    expect(result.current.pins).toEqual([target]);
  });

  it("reloads after Runtime restart and ignores results from the old Runtime epoch", async () => {
    const oldResponse = deferred<SidebarPinsResponse>();
    list.mockResolvedValue({ items: [target], throughEventId: 20 });
    setPinned.mockReturnValue(oldResponse.promise);
    const { result, rerender } = renderHook(({ enabled }) => useSidebarPins(enabled), { initialProps: { enabled: true } });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    act(() => { void result.current.setPinned(target, false); });
    rerender({ enabled: false });
    list.mockResolvedValue({ items: [target], throughEventId: 1 });
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.loaded).toBe(true));
    await act(async () => { oldResponse.resolve({ items: [], throughEventId: 21 }); });
    expect(result.current.pins).toEqual([target]);
  });
});
