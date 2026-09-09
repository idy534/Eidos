import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useNavigationHistory } from "./useNavigationHistory.js";

describe("useNavigationHistory", () => {
  it("initializes with empty history and disabled navigation", () => {
    const { result } = renderHook(() => useNavigationHistory());
    const [state] = result.current;

    expect(state.canGoBack).toBe(false);
    expect(state.canGoForward).toBe(false);
    expect(state.history).toEqual([]);
    expect(state.currentIndex).toBe(-1);
  });

  it("pushes sessions and updates navigation bounds", () => {
    const { result } = renderHook(() => useNavigationHistory());

    act(() => {
      result.current[1].pushSession("session-1");
    });

    expect(result.current[0].canGoBack).toBe(false);
    expect(result.current[0].canGoForward).toBe(false);
    expect(result.current[0].history).toEqual(["session-1"]);
    expect(result.current[0].currentIndex).toBe(0);

    act(() => {
      result.current[1].pushSession("session-2");
    });

    expect(result.current[0].canGoBack).toBe(true);
    expect(result.current[0].canGoForward).toBe(false);
    expect(result.current[0].history).toEqual(["session-1", "session-2"]);
    expect(result.current[0].currentIndex).toBe(1);
  });

  it("ignores consecutive duplicate pushes", () => {
    const { result } = renderHook(() => useNavigationHistory());

    act(() => {
      result.current[1].pushSession("session-1");
      result.current[1].pushSession("session-1");
    });

    expect(result.current[0].history).toEqual(["session-1"]);
    expect(result.current[0].currentIndex).toBe(0);
  });

  it("navigates back and forward correctly", () => {
    const onNavigate = vi.fn();
    const { result } = renderHook(() => useNavigationHistory(onNavigate));

    act(() => {
      result.current[1].pushSession("s-1");
      result.current[1].pushSession("s-2");
      result.current[1].pushSession("s-3");
    });

    expect(result.current[0].canGoBack).toBe(true);
    expect(result.current[0].canGoForward).toBe(false);

    let target: string | undefined;
    act(() => {
      target = result.current[1].goBack();
    });

    expect(target).toBe("s-2");
    expect(onNavigate).toHaveBeenCalledWith("s-2");
    expect(result.current[0].canGoBack).toBe(true);
    expect(result.current[0].canGoForward).toBe(true);
    expect(result.current[0].currentIndex).toBe(1);

    act(() => {
      target = result.current[1].goBack();
    });

    expect(target).toBe("s-1");
    expect(onNavigate).toHaveBeenCalledWith("s-1");
    expect(result.current[0].canGoBack).toBe(false);
    expect(result.current[0].canGoForward).toBe(true);
    expect(result.current[0].currentIndex).toBe(0);

    act(() => {
      target = result.current[1].goForward();
    });

    expect(target).toBe("s-2");
    expect(onNavigate).toHaveBeenCalledWith("s-2");
    expect(result.current[0].canGoBack).toBe(true);
    expect(result.current[0].canGoForward).toBe(true);
    expect(result.current[0].currentIndex).toBe(1);
  });

  it("truncates forward history on new push after back navigation", () => {
    const { result } = renderHook(() => useNavigationHistory());

    act(() => {
      result.current[1].pushSession("s-1");
      result.current[1].pushSession("s-2");
      result.current[1].pushSession("s-3");
    });

    act(() => {
      result.current[1].goBack(); // now at s-2
    });

    act(() => {
      result.current[1].pushSession("s-4");
    });

    expect(result.current[0].history).toEqual(["s-1", "s-2", "s-4"]);
    expect(result.current[0].currentIndex).toBe(2);
    expect(result.current[0].canGoBack).toBe(true);
    expect(result.current[0].canGoForward).toBe(false);
  });

  it("removes deleted session from history and updates index", () => {
    const { result } = renderHook(() => useNavigationHistory());

    act(() => {
      result.current[1].pushSession("s-1");
      result.current[1].pushSession("s-2");
      result.current[1].pushSession("s-3");
    });

    act(() => {
      result.current[1].removeSession("s-2");
    });

    expect(result.current[0].history).toEqual(["s-1", "s-3"]);
    expect(result.current[0].currentIndex).toBe(1);
  });

  it("triggers back/forward via keyboard shortcuts Cmd+[ and Cmd+]", () => {
    const onNavigate = vi.fn();
    const { result } = renderHook(() => useNavigationHistory(onNavigate));

    act(() => {
      result.current[1].pushSession("s-1");
      result.current[1].pushSession("s-2");
    });

    // Cmd+[
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "[",
          metaKey: true,
          bubbles: true,
        }),
      );
    });

    expect(onNavigate).toHaveBeenCalledWith("s-1");
    expect(result.current[0].currentIndex).toBe(0);

    // Cmd+]
    act(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "]",
          metaKey: true,
          bubbles: true,
        }),
      );
    });

    expect(onNavigate).toHaveBeenCalledWith("s-2");
    expect(result.current[0].currentIndex).toBe(1);
  });

  it("supports init and replaceCurrent for draft lifecycle", () => {
    const { result } = renderHook(() => useNavigationHistory());

    // init sets the first entry
    act(() => {
      result.current[1].init("session-1");
    });
    expect(result.current[0].history).toEqual(["session-1"]);
    expect(result.current[0].currentIndex).toBe(0);

    // subsequent inits are ignored
    act(() => {
      result.current[1].init("session-other");
    });
    expect(result.current[0].history).toEqual(["session-1"]);

    // navigate to draft
    act(() => {
      result.current[1].navigateTo("draft");
    });
    expect(result.current[0].history).toEqual(["session-1", "draft"]);
    expect(result.current[0].currentIndex).toBe(1);

    // replaceCurrent replaces draft with real session id when materialized
    act(() => {
      result.current[1].replaceCurrent("session-2");
    });
    expect(result.current[0].history).toEqual(["session-1", "session-2"]);
    expect(result.current[0].currentIndex).toBe(1);
    expect(result.current[0].canGoBack).toBe(true);
    expect(result.current[0].canGoForward).toBe(false);

    // navigate back to session-1
    act(() => {
      result.current[1].goBack();
    });
    expect(result.current[0].currentIndex).toBe(0);
    expect(result.current[0].canGoBack).toBe(false);
    expect(result.current[0].canGoForward).toBe(true);

    // navigate forward to session-2
    act(() => {
      result.current[1].goForward();
    });
    expect(result.current[0].currentIndex).toBe(1);
    expect(result.current[0].canGoBack).toBe(true);
    expect(result.current[0].canGoForward).toBe(false);
  });
});
