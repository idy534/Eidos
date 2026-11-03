import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useStableHandlers } from "./useStableHandlers.js";

describe("useStableHandlers", () => {
  it("keeps handler identity across renders while calling the latest closure", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { result, rerender } = renderHook(
      ({ onSelect }: { onSelect: (id: string) => void }) => useStableHandlers({ onSelect }),
      { initialProps: { onSelect: first } },
    );
    const initial = result.current;

    rerender({ onSelect: second });

    expect(result.current).toBe(initial);
    expect(result.current.onSelect).toBe(initial.onSelect);
    result.current.onSelect("session-1");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith("session-1");
  });
});
