import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRunController } from "./useRunController.js";
import type { EidosRuntimeAPI, Run, SessionSnapshot } from "../contracts.js";

const mockSnapshotA: SessionSnapshot = {
  session: { id: "session-A", title: "Session A", workspaceRoot: "/ws/a", createdAt: 1000, updatedAt: 1000 },
  runs: [],
  items: [],
};

const mockSnapshotB: SessionSnapshot = {
  session: { id: "session-B", title: "Session B", workspaceRoot: "/ws/b", createdAt: 1000, updatedAt: 1000 },
  runs: [],
  items: [],
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

describe("useRunController real behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  function setupMockRuntime(overrides: Partial<EidosRuntimeAPI> = {}) {
    const api: Partial<EidosRuntimeAPI> = {
      startRun: vi.fn().mockResolvedValue(mockRunA),
      continueRun: vi.fn().mockResolvedValue({ ...mockRunA, status: "running" }),
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

  describe("Continue behavior", () => {
    it("waiting_user_input calls continueRun instead of startRun", async () => {
      const startRunSpy = vi.fn();
      const continueRunSpy = vi.fn().mockResolvedValue({ ...mockRunA, status: "running" });
      setupMockRuntime({ startRun: startRunSpy, continueRun: continueRunSpy });

      const snapshotWaiting: SessionSnapshot = {
        ...mockSnapshotA,
        runs: [{
          ...mockRunA,
          status: "waiting_user_input",
          allowedActions: ["continue", "cancel"],
        }],
      };

      const { result } = renderHook(() => useRunController(snapshotWaiting, true));

      act(() => {
        result.current[1].setInput("Supplemental info");
      });

      await act(async () => {
        await result.current[1].submitInput({
          snapshot: snapshotWaiting,
          selectedModelId: "deepseek-v4-flash",
          isStorageReady: true,
        });
      });

      expect(continueRunSpy).toHaveBeenCalledWith("run-A", "Supplemental info");
      expect(startRunSpy).not.toHaveBeenCalled();
    });
  });

  describe("Session isolation", () => {
    it("Session A and Session B inputs remain isolated on switch", () => {
      const { result, rerender } = renderHook(
        ({ snap }) => useRunController(snap, true),
        { initialProps: { snap: mockSnapshotA } },
      );

      act(() => {
        result.current[1].setInput("Session A draft");
      });

      expect(result.current[0].input).toBe("Session A draft");

      // Switch to Session B
      rerender({ snap: mockSnapshotB });
      expect(result.current[0].input).toBe("");

      act(() => {
        result.current[1].setInput("Session B draft");
      });

      expect(result.current[0].input).toBe("Session B draft");

      // Switch back to Session A
      rerender({ snap: mockSnapshotA });
      expect(result.current[0].input).toBe("Session A draft");
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
        const p1 = result.current[1].cancelRun("run-A");
        const p2 = result.current[1].cancelRun("run-A");
        await Promise.all([p1, p2]);
      });

      expect(cancelSpy).toHaveBeenCalledTimes(1);
      expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
      expect(result.current[0].cancelingRunId).toBeUndefined();
    });
  });
});
