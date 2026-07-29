import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRunController } from "./useRunController.js";
import type { EidosRuntimeAPI, Run, SessionSnapshot } from "../contracts.js";

const mockSnapshotA: SessionSnapshot = {
  session: { id: "session-A", title: "Session A", workspaceRoot: "/ws/a", createdAt: 1000, updatedAt: 1000 },
  runs: [],
  items: [],
  stepResolutions: [],
};

const mockSnapshotB: SessionSnapshot = {
  session: { id: "session-B", title: "Session B", workspaceRoot: "/ws/b", createdAt: 1000, updatedAt: 1000 },
  runs: [],
  items: [],
  stepResolutions: [],
};

const mockRunA: Run = {
  id: "run-A",
  sessionId: "session-A",
  status: "running",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 1000,
  startedAt: 1000,
  updatedAt: 1000,
  allowedActions: ["cancel"],
};

const mockRunB: Run = {
  id: "run-B",
  sessionId: "session-B",
  status: "running",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 2000,
  startedAt: 2000,
  updatedAt: 2000,
  allowedActions: ["cancel"],
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useRunController real behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupMockRuntime(overrides: Partial<EidosRuntimeAPI> = {}) {
    const api: Partial<EidosRuntimeAPI> = {
      startRun: vi.fn().mockResolvedValue(mockRunA),
      cancelRun: vi.fn().mockResolvedValue({ ...mockRunA, status: "canceled" }),
      ...overrides,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  describe("Atomic submission & Locking", () => {
    it("Two synchronous submitInput calls invoke startRun once", async () => {
      const startRunSpy = vi.fn().mockImplementation(
        () => new Promise((r) => setTimeout(() => r(mockRunA), 10)),
      );
      setupMockRuntime({ startRun: startRunSpy });

      const { result } = renderHook(() => useRunController(mockSnapshotA, true));

      act(() => {
        result.current[1].setInput("Do task A");
      });

      await act(async () => {
        const p1 = result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
        const p2 = result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
        await Promise.all([p1, p2]);
      });

      expect(startRunSpy).toHaveBeenCalledTimes(1);
    });

    it("Lock releases after success and after failure, allowing subsequent submission", async () => {
      const api = setupMockRuntime({
        startRun: vi.fn().mockRejectedValueOnce(new Error("Transient IPC Error")).mockResolvedValue(mockRunA),
      });

      const { result } = renderHook(() => useRunController(mockSnapshotA, true));

      act(() => {
        result.current[1].setInput("Do task");
      });

      // First submission fails
      await act(async () => {
        await result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
      });

      expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
      expect(result.current[0].isSubmitting).toBe(false);
      expect(result.current[0].input).toBe("Do task"); // Input preserved on failure

      // Retry submission succeeds
      await act(async () => {
        await result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
      });

      expect(api.startRun).toHaveBeenCalledTimes(2);
      expect(result.current[0].input).toBe(""); // Cleared on success
    });
  });

  describe("Start behavior", () => {
    it("Empty input or read-only storage does not call IPC", async () => {
      const startRunSpy = vi.fn();
      setupMockRuntime({ startRun: startRunSpy });

      const { result } = renderHook(() => useRunController(mockSnapshotA, false)); // Storage not ready

      act(() => {
        result.current[1].setInput("Task text");
      });

      await act(async () => {
        await result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: false,
        });
      });

      expect(startRunSpy).not.toHaveBeenCalled();
    });

    it("Returned Run is projected immediately and input cleared on match", async () => {
      const projectRunSpy = vi.fn();
      setupMockRuntime();

      const { result } = renderHook(() => useRunController(mockSnapshotA, true));

      act(() => {
        result.current[1].setInput("Run prompt");
      });

      await act(async () => {
        await result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
          onRunProjected: projectRunSpy,
        });
      });

      expect(projectRunSpy).toHaveBeenCalledWith("session-A", mockRunA);
      expect(result.current[0].input).toBe("");
    });

    it("Mismatched sessionId response does not clear input or project cross-session", async () => {
      const mismatchedRun = { ...mockRunA, sessionId: "session-OTHER" };
      const projectRunSpy = vi.fn();
      setupMockRuntime({ startRun: vi.fn().mockResolvedValue(mismatchedRun) });

      const { result } = renderHook(() => useRunController(mockSnapshotA, true));

      act(() => {
        result.current[1].setInput("Draft text");
      });

      await act(async () => {
        await result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
          onRunProjected: projectRunSpy,
        });
      });

      expect(projectRunSpy).not.toHaveBeenCalled();
      expect(result.current[0].input).toBe("Draft text"); // Preserved
    });
  });

  describe("Run State Isolation & Lock Contention Sequence", () => {
    it("completes full isolation and lock contention assertion sequence", async () => {
      let resolveRunA: ((run: Run) => void) | undefined;
      let rejectRunA: ((err: Error) => void) | undefined;

      const startRunSpy = vi.fn().mockImplementation((sessionId: string) => {
        if (sessionId === "session-A") {
          return new Promise<Run>((resolve, reject) => {
            resolveRunA = resolve;
            rejectRunA = reject;
          });
        }
        return Promise.resolve(mockRunB);
      });

      setupMockRuntime({ startRun: startRunSpy });

      // 1. Render Session A
      const { result, rerender } = renderHook(
        ({ snap }) => useRunController(snap, true),
        { initialProps: { snap: mockSnapshotA } },
      );

      // 2. Enter A draft
      act(() => {
        result.current[1].setInput("Session A draft prompt");
      });

      // 3. Start A with unresolved Promise
      let submitPromiseA: Promise<void>;
      act(() => {
        submitPromiseA = result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
      });

      // 4. Verify A is submitting and displays Start state
      expect(result.current[0].isSubmitting).toBe(true);
      expect(result.current[0].submitKind).toBe("start");

      // 5. Switch to Session B
      rerender({ snap: mockSnapshotB });

      // 6. Verify B: not visually submitting, no A start state, retains own draft, no A error
      expect(result.current[0].isSubmitting).toBe(false);
      expect(result.current[0].submitKind).toBeUndefined();
      expect(result.current[0].input).toBe("");
      expect(result.current[0].error).toBeUndefined();

      // Enter B draft
      act(() => {
        result.current[1].setInput("Session B draft prompt");
      });

      // 7 & 8. Attempt B submission while A owns the lock
      await act(async () => {
        await result.current[1].submitInput({
          snapshot: mockSnapshotB,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
      });

      expect(startRunSpy).toHaveBeenCalledTimes(1); // No second IPC call!
      expect(result.current[0].input).toBe("Session B draft prompt"); // B draft remains
      expect(result.current[0].error).toBe("另一个任务正在启动，请稍后重试。"); // Explicit local busy feedback

      // 9 & 10. Reject A and verify error belongs only to A
      act(() => {
        rejectRunA?.(new Error("RPC failed for A"));
      });
      await act(async () => {
        await submitPromiseA.catch(() => {});
      });

      // While viewing B, B's error remains the busy feedback
      expect(result.current[0].error).toBe("另一个任务正在启动，请稍后重试。");

      // Switch to A -> A displays its own error
      rerender({ snap: mockSnapshotA });
      expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");

      // 11 & 12. Resolve later A retry -> only A's draft is cleared
      const secondStartSpy = vi.fn().mockResolvedValue(mockRunA);
      setupMockRuntime({ startRun: secondStartSpy });

      await act(async () => {
        await result.current[1].submitInput({
          snapshot: mockSnapshotA,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
      });

      expect(result.current[0].input).toBe(""); // A draft cleared

      // Switch back to B -> B's draft is intact
      rerender({ snap: mockSnapshotB });
      expect(result.current[0].input).toBe("Session B draft prompt");
    });
  });

  describe("Cancellation locking & errors", () => {
    it("Duplicate cancel clicks invoke cancelRun once and failure exposes Run error", async () => {
      const cancelSpy = vi.fn().mockImplementation(
        () => new Promise((_, reject) => setTimeout(() => reject(new Error("Cannot cancel")), 10)),
      );
      setupMockRuntime({ cancelRun: cancelSpy });

      const { result } = renderHook(() => useRunController(mockSnapshotA, true));

      await act(async () => {
        const p1 = result.current[1].cancelRun({ runId: "run-A", sessionId: "session-A" });
        const p2 = result.current[1].cancelRun({ runId: "run-A", sessionId: "session-A" });
        await Promise.all([p1, p2]);
      });

      expect(cancelSpy).toHaveBeenCalledTimes(1);
      expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
      expect(result.current[0].cancelingRunId).toBeUndefined();
    });
  });
});
