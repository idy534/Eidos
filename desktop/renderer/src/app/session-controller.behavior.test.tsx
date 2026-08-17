import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSessionController } from "./useSessionController.js";
import type { EidosRuntimeAPI, Session, SessionSnapshot } from "../contracts.js";

const mockSession1: Session = {
  id: "session-1",
  title: "Session One",
  workspaceRoot: "/workspace/one",
  taskStatus: "new",
  createdAt: 1000,
  updatedAt: 1000,
};

const mockSession2: Session = {
  id: "session-2",
  title: "Session Two",
  workspaceRoot: "/workspace/two",
  taskStatus: "new",
  createdAt: 2000,
  updatedAt: 2000,
};

const mockSnapshot1: SessionSnapshot = {
  session: mockSession1,
  runs: [],
  items: [],
  stepResolutions: [],
};

const mockSnapshot2: SessionSnapshot = {
  session: mockSession2,
  runs: [],
  items: [],
  stepResolutions: [],
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useSessionController real Hook behavior", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  function setupMockRuntime(overrides: Partial<EidosRuntimeAPI> = {}) {
    const api: Partial<EidosRuntimeAPI> = {
      listSessions: vi.fn().mockResolvedValue({ items: [mockSession1, mockSession2] }),
      readSession: vi.fn().mockImplementation((id: string) => Promise.resolve(id === "session-1" ? mockSnapshot1 : mockSnapshot2)),
      listEvents: vi.fn().mockResolvedValue({ items: [], throughEventId: 0, hasMore: false }),
      selectWorkspace: vi.fn().mockResolvedValue("/workspace/new"),
      createSession: vi.fn().mockResolvedValue(mockSession1),
      renameSession: vi.fn().mockImplementation((id, title) => Promise.resolve({ ...mockSession1, id, title })),
      deleteSession: vi.fn().mockResolvedValue({ deletedSessionId: "session-1" }),
      ...overrides,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  it("1 & 2. Two synchronous projectless createSession() calls call IPC once", async () => {
    const createSpy = vi.fn().mockResolvedValue(mockSession1);

    setupMockRuntime({
      createSession: createSpy,
    });

    const { result } = renderHook(() => useSessionController());

    let p1: Promise<SessionSnapshot | undefined>;
    let p2: Promise<SessionSnapshot | undefined>;

    act(() => {
      p1 = result.current[1].createSession(null);
      p2 = result.current[1].createSession(null);
    });

    await act(async () => { await Promise.all([p1, p2]); });

    expect(createSpy).toHaveBeenCalledTimes(1);
    expect(createSpy).toHaveBeenCalledWith(null);
    expect(await p2!).toBeUndefined(); // Second synchronous call returns undefined
  });

  it("3. Pending becomes true before projectless Session creation settles", async () => {
    let resolveCreate!: (value: Session) => void;
    setupMockRuntime({
      createSession: () => new Promise((resolve) => { resolveCreate = resolve; }),
    });

    const { result } = renderHook(() => useSessionController());
    expect(result.current[0].pending.creatingSession).toBeUndefined();

    let p: Promise<SessionSnapshot | undefined>;
    act(() => {
      p = result.current[1].createSession(null);
    });

    expect(result.current[0].pending.creatingSession).toBe(true);

    await act(async () => {
      resolveCreate(mockSession1);
      await p;
    });

    expect(result.current[0].pending.creatingSession).toBeUndefined();
  });

  it("4. Projectless Session creation releases the lock and pending state", async () => {
    const createSpy = vi.fn().mockResolvedValue(mockSession1);
    setupMockRuntime({ createSession: createSpy });

    const { result } = renderHook(() => useSessionController());

    await act(async () => {
      await result.current[1].createSession(null);
    });

    expect(createSpy).toHaveBeenCalledWith(null);
    expect(result.current[0].pending.creatingSession).toBeUndefined();
  });

  it("5. Projectless Session failure releases the lock", async () => {
    setupMockRuntime({ createSession: vi.fn().mockRejectedValue(new Error("Session creation error")) });

    const { result } = renderHook(() => useSessionController());

    await act(async () => {
      const snap = await result.current[1].createSession(null);
      expect(snap).toBeUndefined();
    });

    expect(result.current[0].pending.creatingSession).toBeUndefined();
    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
  });

  it("6. Session creation IPC failure releases the lock", async () => {
    setupMockRuntime({ createSession: vi.fn().mockRejectedValue(new Error("Failed to create session")) });

    const { result } = renderHook(() => useSessionController());

    await act(async () => {
      const snap = await result.current[1].createSession();
      expect(snap).toBeUndefined();
    });

    expect(result.current[0].pending.creatingSession).toBeUndefined();
    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
  });

  it("7. Snapshot load failure releases the lock", async () => {
    setupMockRuntime({ readSession: vi.fn().mockRejectedValue(new Error("Read session error")) });

    const { result } = renderHook(() => useSessionController());

    await act(async () => {
      const snap = await result.current[1].createSession("/workspace/new");
      expect(snap).toBeUndefined();
    });

    expect(result.current[0].pending.creatingSession).toBeUndefined();
    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
  });

  it("8. A later retry succeeds after failure", async () => {
    const api = setupMockRuntime({
      createSession: vi.fn().mockRejectedValueOnce(new Error("First fail")).mockResolvedValue(mockSession1),
    });

    const { result } = renderHook(() => useSessionController());

    // First attempt fails
    await act(async () => {
      await result.current[1].createSession("/workspace/new");
    });
    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");

    // Retry succeeds
    await act(async () => {
      const snap = await result.current[1].createSession("/workspace/new");
      expect(snap).toEqual(mockSnapshot1);
    });

    expect(api.createSession).toHaveBeenCalledTimes(2);
    expect(result.current[0].error).toBeUndefined();
  });

  it("9. A stale Session A selection cannot clear Session B pending state", async () => {
    let resolveReadA!: (val: SessionSnapshot) => void;
    let resolveReadB!: (val: SessionSnapshot) => void;

    setupMockRuntime({
      readSession: vi.fn().mockImplementation((id: string) => {
        if (id === "session-1") return new Promise((r) => { resolveReadA = r; });
        return new Promise((r) => { resolveReadB = r; });
      }),
    });

    const { result } = renderHook(() => useSessionController());

    let pA: Promise<SessionSnapshot | undefined>;
    let pB: Promise<SessionSnapshot | undefined>;

    // Select A
    act(() => {
      pA = result.current[1].selectSession(mockSession1);
    });
    expect(result.current[0].pending.selectingSessionId).toBe("session-1");

    // Rapidly select B (supercedes A)
    act(() => {
      pB = result.current[1].selectSession(mockSession2);
    });
    expect(result.current[0].pending.selectingSessionId).toBe("session-2");

    // Stale A resolves first -> must NOT clear B's selectingSessionId
    await act(async () => {
      resolveReadA(mockSnapshot1);
      await pA;
    });

    expect(result.current[0].pending.selectingSessionId).toBe("session-2");

    // B resolves -> now clears selectingSessionId
    await act(async () => {
      resolveReadB(mockSnapshot2);
      await pB;
    });

    expect(result.current[0].pending.selectingSessionId).toBeUndefined();
  });

  it("10. A failed target Session selection returns undefined and does not open rename mode target", async () => {
    setupMockRuntime({ readSession: vi.fn().mockRejectedValue(new Error("RPC failed")) });

    const { result } = renderHook(() => useSessionController());

    await act(async () => {
      const loaded = await result.current[1].selectSession(mockSession1);
      expect(loaded).toBeUndefined();
    });

    expect(result.current[0].snapshot).toBeUndefined();
  });

  it("11. Rename IPC receives the intended Session ID", async () => {
    const renameSpy = vi.fn().mockResolvedValue({ ...mockSession1, title: "Renamed Title" });
    setupMockRuntime({ renameSession: renameSpy });

    const { result } = renderHook(() => useSessionController());

    await act(async () => {
      await result.current[1].renameSession("session-1", "Renamed Title");
    });

    expect(renameSpy).toHaveBeenCalledWith("session-1", "Renamed Title");
  });

  it("12. Delete failure returns its actual error", async () => {
    setupMockRuntime({ deleteSession: vi.fn().mockRejectedValue(new Error("Permission denied to delete")) });

    const { result } = renderHook(() => useSessionController());

    let deleteRes: { confirmed: boolean; error?: string } | undefined;
    await act(async () => {
      deleteRes = await result.current[1].deleteSession(mockSession1);
    });

    expect(deleteRes?.confirmed).toBe(false);
    expect(deleteRes?.error).toBe("操作失败，请查看 Runtime 日志。");
  });

  it("Same-Session selection deduplication: A1 and A2 share one Promise and call readSession once", async () => {
    let resolveRead!: (snapshot: SessionSnapshot) => void;
    const readSpy = vi.fn().mockImplementation(() => new Promise((r) => { resolveRead = r; }));

    setupMockRuntime({ readSession: readSpy });
    const { result } = renderHook(() => useSessionController());

    let pA1!: Promise<SessionSnapshot | undefined>;
    let pA2!: Promise<SessionSnapshot | undefined>;

    act(() => {
      pA1 = result.current[1].selectSession(mockSession1);
      pA2 = result.current[1].selectSession(mockSession1);
    });

    expect(readSpy).toHaveBeenCalledTimes(1);
    expect(result.current[0].pending.selectingSessionId).toBe("session-1");

    await act(async () => {
      resolveRead(mockSnapshot1);
      const [res1, res2] = await Promise.all([pA1, pA2]);
      expect(res1).toEqual(mockSnapshot1);
      expect(res2).toEqual(mockSnapshot1);
    });

    expect(result.current[0].pending.selectingSessionId).toBeUndefined();
    expect(result.current[0].snapshot).toEqual(mockSnapshot1);
  });

  it("A -> B -> A sequence creates a new operation and accepts only the final A result", async () => {
    const reads: Array<{ id: string; resolve: (s: SessionSnapshot) => void }> = [];
    const readSpy = vi.fn().mockImplementation((id: string) => {
      return new Promise<SessionSnapshot>((resolve) => {
        reads.push({ id, resolve });
      });
    });

    setupMockRuntime({ readSession: readSpy });
    const { result } = renderHook(() => useSessionController());

    let pA1!: Promise<SessionSnapshot | undefined>;
    let pB!: Promise<SessionSnapshot | undefined>;
    let pA2!: Promise<SessionSnapshot | undefined>;

    // 1. Select A1
    act(() => {
      pA1 = result.current[1].selectSession(mockSession1);
    });

    // 2. Select B
    act(() => {
      pB = result.current[1].selectSession(mockSession2);
    });

    // 3. Select A2 (new token for A)
    act(() => {
      pA2 = result.current[1].selectSession(mockSession1);
    });

    expect(readSpy).toHaveBeenCalledTimes(3);
    expect(pA1).not.toBe(pA2);

    // Resolve A1 (stale)
    await act(async () => {
      reads[0]?.resolve(mockSnapshot1);
      const resA1 = await pA1;
      expect(resA1).toBeUndefined();
    });

    // Resolve B (stale)
    await act(async () => {
      reads[1]?.resolve(mockSnapshot2);
      const resB = await pB;
      expect(resB).toBeUndefined();
    });

    // Resolve A2 (current)
    await act(async () => {
      reads[2]?.resolve(mockSnapshot1);
      const resA2 = await pA2;
      expect(resA2).toEqual(mockSnapshot1);
    });

    expect(result.current[0].snapshot).toEqual(mockSnapshot1);
    expect(result.current[0].navigationSessionId).toBe("session-1");
  });

  it("Stale failures do not set error on current selection, and retry works", async () => {
    let rejectA!: (err: Error) => void;
    let resolveB!: (val: SessionSnapshot) => void;

    const readSpy = vi.fn().mockImplementation((id: string) => {
      if (id === "session-1") {
        return new Promise((_, reject) => { rejectA = reject; });
      }
      return new Promise((resolve) => { resolveB = resolve; });
    });

    setupMockRuntime({ readSession: readSpy });
    const { result } = renderHook(() => useSessionController());

    let pA!: Promise<SessionSnapshot | undefined>;
    let pB!: Promise<SessionSnapshot | undefined>;

    act(() => {
      pA = result.current[1].selectSession(mockSession1);
      pB = result.current[1].selectSession(mockSession2);
    });

    // Reject stale A
    await act(async () => {
      rejectA(new Error("Network failure A"));
      await pA;
    });

    // Error must NOT be displayed for current selection B
    expect(result.current[0].error).toBeUndefined();

    // Resolve B
    await act(async () => {
      resolveB(mockSnapshot2);
      await pB;
    });

    expect(result.current[0].snapshot).toEqual(mockSnapshot2);
    expect(result.current[0].error).toBeUndefined();

    // Retry A after B settles
    setupMockRuntime({
      readSession: vi.fn().mockResolvedValue(mockSnapshot1),
    });

    let pA_retry!: Promise<SessionSnapshot | undefined>;
    await act(async () => {
      pA_retry = await result.current[1].selectSession(mockSession1);
    });

    expect(pA_retry).toEqual(mockSnapshot1);
    expect(result.current[0].snapshot).toEqual(mockSnapshot1);
  });
});
