import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSessionController } from "./useSessionController.js";
import type { EidosRuntimeAPI, Session, SessionListResult, SessionSnapshot } from "../contracts.js";

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
      listProjects: vi.fn().mockResolvedValue({ items: [] }),
      listSessions: vi.fn().mockResolvedValue({ items: [mockSession1, mockSession2] }),
      readSession: vi.fn().mockImplementation((id: string) => Promise.resolve(id === "session-1" ? mockSnapshot1 : mockSnapshot2)),
      listEvents: vi.fn().mockResolvedValue({ items: [], throughEventId: 0, hasMore: false }),
      selectWorkspace: vi.fn().mockResolvedValue("/workspace/new"),
      createSession: vi.fn().mockResolvedValue(mockSession1),
      renameSession: vi.fn().mockImplementation((id, title) => Promise.resolve({ ...mockSession1, id, title })),
      deleteSession: vi.fn().mockResolvedValue({ deletedSessionId: "session-1" }),
      deleteProject: vi.fn().mockResolvedValue({ deletedProjectId: "project-1" }),
      ...overrides,
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
    return api;
  }

  it("keeps the source open on fork failure and reuses operationId when retrying", async () => {
    const forkSession = vi.fn().mockRejectedValueOnce(new Error("capture failed")).mockResolvedValueOnce(mockSession2);
    const startRun = vi.fn();
    setupMockRuntime({ forkSession, startRun });
    const { result } = renderHook(() => useSessionController());
    await act(async () => { await result.current[1].loadSessions(); await result.current[1].selectSession(mockSession1); });
    await act(async () => { await result.current[1].forkSession(mockSession1.id, "answer-1", { kind: "same_workspace" }); });
    expect(result.current[0].snapshot?.session.id).toBe(mockSession1.id);
    expect(result.current[0].pending.creatingSession).toBe(false);
    await act(async () => { await result.current[1].forkSession(mockSession1.id, "answer-1", { kind: "same_workspace" }); });
    expect(forkSession.mock.calls[0]?.[0]).toEqual(forkSession.mock.calls[1]?.[0]);
    expect(result.current[0].snapshot?.session.id).toBe(mockSession2.id);
    expect(result.current[0].sessions.filter((session) => session.id === mockSession2.id)).toHaveLength(1);
    expect(startRun).not.toHaveBeenCalled();
  });

  it("admits one fork while the first request is still in progress", async () => {
    let resolveFork!: (session: Session) => void;
    const forkSession = vi.fn().mockImplementation(() => new Promise<Session>((resolve) => { resolveFork = resolve; }));
    setupMockRuntime({ forkSession });
    const { result } = renderHook(() => useSessionController());
    let first!: Promise<SessionSnapshot | undefined>;
    await act(async () => {
      first = result.current[1].forkSession(mockSession1.id, "answer", { kind: "same_workspace" });
      await result.current[1].forkSession(mockSession1.id, "answer", { kind: "same_workspace" });
    });
    expect(forkSession).toHaveBeenCalledTimes(1);
    expect(result.current[0].pending.creatingSession).toBe(true);
    await act(async () => { resolveFork(mockSession2); await first; });
    expect(result.current[0].snapshot?.session.id).toBe(mockSession2.id);
  });

  it("loads older transcript records and preserves current facts while merging", async () => {
    const entry = (id: string) => ({ id, sessionId: mockSession1.id, runId: "run", ordinal: 1,
      kind: "assistant_message" as const, status: "completed" as const, createdAt: 1, content: id });
    const readSession = vi.fn().mockResolvedValueOnce({ ...mockSnapshot1, items: [entry("recent")], previousItemId: "recent" })
      .mockResolvedValueOnce({ ...mockSnapshot1, items: [entry("old"), entry("recent")], previousItemId: "old" });
    setupMockRuntime({ readSession });
    const { result } = renderHook(() => useSessionController());
    await act(async () => { await result.current[1].selectSession(mockSession1); });
    await act(async () => {
      result.current[1].setSnapshot((snapshot) => ({ ...snapshot!, session: { ...snapshot!.session, title: "new title" } }));
    });
    await act(async () => { await result.current[1].loadOlderSession(); });
    expect(result.current[0].snapshot?.items.map((item) => item.id)).toEqual(["old", "recent"]);
    expect(result.current[0].snapshot?.session.title).toBe("new title");
    expect(result.current[0].snapshot?.previousItemId).toBe("old");
    expect(readSession).toHaveBeenLastCalledWith(mockSession1.id, { itemLimit: 500, beforeItemId: "recent" });
  });

  it("merges older compaction points without replacing the latest lifecycle status", async () => {
    const activity = { requestId: "compact", runId: "run", startedAt: 1, updatedAt: 3, eventId: 3, status: "succeeded" as const };
    const readSession = vi.fn().mockResolvedValueOnce({ ...mockSnapshot1, previousItemId: "recent", contextCompactions: [activity] })
      .mockResolvedValueOnce({ ...mockSnapshot1, contextCompactions: [
        { ...activity, eventId: 1, updatedAt: 1, status: "running" },
        { ...activity, requestId: "older", startedAt: 0, eventId: 2 },
      ] });
    setupMockRuntime({ readSession });
    const { result } = renderHook(() => useSessionController());
    await act(async () => { await result.current[1].selectSession(mockSession1); });
    await act(async () => { await result.current[1].loadOlderSession(); });
    expect(result.current[0].snapshot?.contextCompactions?.map((entry) => entry.requestId)).toEqual(["older", "compact"]);
    expect(result.current[0].snapshot?.contextCompactions?.at(-1)?.status).toBe("succeeded");
  });

  it("ignores a late history page after navigation to another Session", async () => {
    let resolveHistory!: (snapshot: SessionSnapshot) => void;
    const readSession = vi.fn().mockResolvedValueOnce({ ...mockSnapshot1, previousItemId: "recent" })
      .mockImplementationOnce(() => new Promise<SessionSnapshot>((resolve) => { resolveHistory = resolve; }))
      .mockResolvedValueOnce(mockSnapshot2);
    setupMockRuntime({ readSession });
    const { result } = renderHook(() => useSessionController());
    await act(async () => { await result.current[1].selectSession(mockSession1); });
    let loading!: Promise<void>;
    await act(async () => { loading = result.current[1].loadOlderSession(); });
    await act(async () => { await result.current[1].selectSession(mockSession2); });
    await act(async () => { resolveHistory(mockSnapshot1); await loading; });
    expect(result.current[0].snapshot?.session.id).toBe(mockSession2.id);
  });

  it("loads the page containing the source fork answer before selecting it", async () => {
    const selected = { id: "source-answer", sessionId: mockSession1.id, runId: "old-run", ordinal: 2,
      kind: "assistant_message" as const, status: "completed" as const, createdAt: 1 };
    const readSession = vi.fn().mockResolvedValueOnce({ ...mockSnapshot1, previousItemId: "recent" })
      .mockResolvedValueOnce({ ...mockSnapshot1, items: [selected] });
    setupMockRuntime({ readSession });
    const { result } = renderHook(() => useSessionController());
    await act(async () => { await result.current[1].selectSession(mockSession1, selected.id); });
    expect(result.current[0].snapshot?.items[0]?.id).toBe(selected.id);
    expect(readSession).toHaveBeenLastCalledWith(mockSession1.id, { itemLimit: 500, beforeItemId: "recent" });
  });

  it("loads every Session page so older Projects retain their conversations", async () => {
    const sessions = Array.from({ length: 112 }, (_, index): Session => ({
      ...mockSession1,
      id: `session-${index}`,
      title: `Conversation ${index}`,
      project: {
        id: `project-${Math.floor(index / 15)}`,
        workspaceRoot: `/project/${Math.floor(index / 15)}`,
        gitAvailable: false,
      },
    }));
    let resolveLastPage!: (page: SessionListResult) => void;
    const listSessions = vi.fn()
      .mockResolvedValueOnce({ items: sessions.slice(0, 50), nextCursor: "page-2" })
      .mockResolvedValueOnce({ items: sessions.slice(50, 100), nextCursor: "page-3" })
      .mockImplementationOnce(() => new Promise<SessionListResult>((resolve) => { resolveLastPage = resolve; }));
    setupMockRuntime({ listSessions });
    const { result } = renderHook(() => useSessionController());
    let loading!: Promise<void>;
    await act(async () => { loading = result.current[1].loadSessions(); });

    expect(result.current[0].sessions).toEqual([]);
    expect(listSessions.mock.calls).toEqual([[], ["page-2"], ["page-3"]]);
    await act(async () => {
      resolveLastPage({ items: sessions.slice(100) });
      await loading;
    });
    expect(result.current[0].sessions).toEqual(sessions);
    expect(result.current[0].sessions.at(-1)?.project?.id).toBe("project-7");
  });

  it("preserves the previous complete list if a later page fails", async () => {
    const listSessions = vi.fn()
      .mockResolvedValueOnce({ items: [mockSession1, mockSession2] })
      .mockResolvedValueOnce({ items: [mockSession1], nextCursor: "page-2" })
      .mockRejectedValueOnce(new Error("Page unavailable"));
    setupMockRuntime({ listSessions });
    const { result } = renderHook(() => useSessionController());
    await act(async () => { await result.current[1].loadSessions(); });
    await act(async () => { await result.current[1].loadSessions(); });
    expect(result.current[0].sessions).toEqual([mockSession1, mockSession2]);
    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
  });

  it("stops a repeated pagination cursor without publishing a partial list", async () => {
    const listSessions = vi.fn().mockResolvedValue({ items: [mockSession1], nextCursor: "repeated" });
    setupMockRuntime({ listSessions });
    const { result } = renderHook(() => useSessionController());
    await act(async () => { await result.current[1].loadSessions(); });
    expect(listSessions).toHaveBeenCalledTimes(2);
    expect(result.current[0].sessions).toEqual([]);
    expect(result.current[0].error).toBe("操作失败，请查看 Runtime 日志。");
  });

  it("ignores an older list load after a newer refresh has completed", async () => {
    let resolveOldPage!: (page: SessionListResult) => void;
    const listSessions = vi.fn()
      .mockResolvedValueOnce({ items: [mockSession1], nextCursor: "old-page-2" })
      .mockImplementationOnce(() => new Promise<SessionListResult>((resolve) => { resolveOldPage = resolve; }))
      .mockResolvedValueOnce({ items: [mockSession2] });
    setupMockRuntime({ listSessions });
    const { result } = renderHook(() => useSessionController());
    let older!: Promise<void>;
    await act(async () => { older = result.current[1].loadSessions(); });
    await act(async () => { await result.current[1].loadSessions(); });
    await act(async () => {
      resolveOldPage({ items: [mockSession1], nextCursor: "old-page-3" });
      await older;
    });
    expect(listSessions).toHaveBeenCalledTimes(3);
    expect(result.current[0].sessions).toEqual([mockSession2]);
    expect(result.current[0].error).toBeUndefined();
  });

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

  it("keeps a new Session local until the first Run materializes it", async () => {
    const project = {
      id: "project-eidos",
      name: "Eidos",
      workspaceRoot: "/workspace/eidos",
      gitAvailable: true,
      createdAt: 1,
      updatedAt: 1,
    };
    const createSession = vi.fn().mockResolvedValue(mockSession1);
    setupMockRuntime({ createSession });

    const { result } = renderHook(() => useSessionController());
    act(() => result.current[1].startDraft(project));

    expect(createSession).not.toHaveBeenCalled();
    expect(result.current[0].sessions).toEqual([]);
    expect(result.current[0].draft?.session.project?.id).toBe(project.id);

    await act(async () => {
      await result.current[1].materializeDraft();
    });

    expect(createSession).toHaveBeenCalledWith(project.workspaceRoot, { executionMode: "local" });
    expect(result.current[0].snapshot).toEqual(mockSnapshot1);
    expect(result.current[0].draft).toBeDefined();
  });

  it("passes the first input hint when materializing a projectless draft", async () => {
    const createSession = vi.fn().mockResolvedValue({ ...mockSession1, projectless: true });
    setupMockRuntime({ createSession });
    const { result } = renderHook(() => useSessionController());
    act(() => result.current[1].startDraft(null));
    await act(async () => {
      await result.current[1].materializeDraft("整理销售数据");
    });
    expect(createSession).toHaveBeenCalledWith(null, {
      executionMode: "local", workspaceNameHint: "整理销售数据",
    });
  });

  it("restores the draft when a materialized Session has no Run", async () => {
    const deleteSession = vi.fn().mockResolvedValue({ deletedSessionId: mockSession1.id });
    setupMockRuntime({ deleteSession });

    const { result } = renderHook(() => useSessionController());
    act(() => result.current[1].startDraft());
    const draft = result.current[0].draft!;

    await act(async () => {
      await result.current[1].rollbackMaterializedSession(mockSession1, draft);
    });

    expect(deleteSession).toHaveBeenCalledWith(mockSession1.id);
    expect(result.current[0].snapshot).toBeUndefined();
    expect(result.current[0].draft).toBe(draft);
    expect(result.current[0].sessions).toEqual([]);
  });
});
