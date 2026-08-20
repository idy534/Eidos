import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  EidosRuntimeAPI,
  ProjectGitContext,
  RuntimeNotification,
  Session,
  SessionGitDiff,
  SessionGitStatus,
} from "../contracts.js";
import { useGitReviewController } from "./useGitReviewController.js";


const managedSession: Session = {
  id: "session-a",
  workspaceRoot: "/repository",
  project: {
    id: "project-a",
    workspaceRoot: "/repository",
    gitAvailable: true,
  },
  worktree: {
    worktreeId: "worktree-a",
    projectId: "project-a",
    repositoryRoot: "/repository",
    worktreeRoot: "/managed/a",
    baseRef: "main",
    baseCommit: "a".repeat(40),
    branch: "eidos/a",
    state: "active",
  },
  taskStatus: "new",
  createdAt: 1,
  updatedAt: 1,
};

const gitStatus: SessionGitStatus = {
  worktreeId: "worktree-a",
  branch: "eidos/a",
  head: "b".repeat(40),
  baseRef: "main",
  baseCommit: "a".repeat(40),
  dirty: true,
  stagedCount: 0,
  unstagedCount: 1,
  untrackedCount: 0,
  conflictCount: 0,
  stagedFiles: [],
  unstagedFiles: ["README.md"],
  untrackedFiles: [],
  conflictFiles: [],
  observedAt: 1,
};

const gitDiff: SessionGitDiff = {
  scope: "baseline",
  compareRef: "main",
  baseCommit: "a".repeat(40),
  head: "b".repeat(40),
  dirty: true,
  changedFiles: ["README.md"],
  unifiedDiff: "diff --git a/README.md b/README.md\n",
  diffHash: "diff-hash",
  truncated: false,
  additions: 1,
  deletions: 0,
  statsIncomplete: false,
  observedAt: 1,
};

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useGitReviewController", () => {
  beforeEach(() => {
    vi.useRealTimers();
    const api: Partial<EidosRuntimeAPI> = {
      readSessionGitStatus: vi.fn().mockResolvedValue(gitStatus),
      readSessionGitDiff: vi.fn().mockResolvedValue(gitDiff),
      readProjectGitContext: vi.fn().mockResolvedValue({
        gitAvailable: true,
        currentBranch: "main",
        head: "b".repeat(40),
        branches: ["main", "dev-830-xl"],
        dirty: false,
        changedFileCount: 0,
      } satisfies ProjectGitContext),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("loads structured status and the current repository summary", async () => {
    const { result } = renderHook(() => useGitReviewController({
      ready: true,
      session: managedSession,
    }));

    await waitFor(() => expect(result.current[0].summary?.changedFiles).toEqual(["README.md"]));

    expect(window.eidosRuntime.readSessionGitStatus).toHaveBeenCalledWith("session-a");
    expect(window.eidosRuntime.readSessionGitDiff).toHaveBeenCalledWith("session-a", "baseline");
  });

  it("switches scope and reloads the matching repository summary", async () => {
    const { result } = renderHook(() => useGitReviewController({
      ready: true,
      session: managedSession,
    }));
    await waitFor(() => expect(result.current[0].status).toBeDefined());
    vi.mocked(window.eidosRuntime.readSessionGitStatus).mockClear();
    vi.mocked(window.eidosRuntime.readSessionGitDiff).mockClear();

    act(() => result.current[1].selectScope("head"));

    expect(result.current[0].scope).toBe("head");
    expect(window.eidosRuntime.readSessionGitStatus).not.toHaveBeenCalled();
    expect(window.eidosRuntime.readSessionGitDiff).toHaveBeenCalledWith("session-a", "head");
  });

  it("debounces durable completion refreshes and ignores content deltas", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useGitReviewController({
      ready: true,
      session: managedSession,
    }));
    await act(async () => { await Promise.resolve(); });
    vi.mocked(window.eidosRuntime.readSessionGitStatus).mockClear();
    vi.mocked(window.eidosRuntime.readSessionGitDiff).mockClear();

    const delta: RuntimeNotification = {
      method: "item/delta",
      params: { sessionId: "session-a", runId: "run-a", itemId: "item-a", delta: "x" },
    };
    const completed: RuntimeNotification = {
      method: "item/completed",
      params: {
        sessionId: "session-a",
        runId: "run-a",
        item: {
          id: "item-a",
          sessionId: "session-a",
          runId: "run-a",
          ordinal: 1,
          kind: "file_change",
          status: "completed",
          createdAt: 1,
          completedAt: 2,
        },
      },
    };
    const commandCompleted: RuntimeNotification = {
      ...completed,
      params: {
        ...completed.params,
        item: { ...completed.params.item, id: "item-command", kind: "command_execution" },
      },
    };
    const runCompleted: RuntimeNotification = {
      method: "run/completed",
      params: {
        sessionId: "session-a",
        run: {
          id: "run-a",
          sessionId: "session-a",
          userInput: "change files",
          status: "succeeded",
          modelId: "deepseek-v4-flash",
          modelStepCount: 1,
          createdAt: 1,
          startedAt: 1,
          updatedAt: 2,
          completedAt: 2,
        },
      },
    };

    act(() => {
      result.current[1].handleNotification(delta);
      result.current[1].handleNotification(completed);
      result.current[1].handleNotification(commandCompleted);
      result.current[1].handleNotification(runCompleted);
      result.current[1].handleNotification(completed);
      vi.advanceTimersByTime(150);
    });
    await act(async () => { await Promise.resolve(); });

    expect(window.eidosRuntime.readSessionGitStatus).toHaveBeenCalledOnce();
    expect(window.eidosRuntime.readSessionGitDiff).toHaveBeenCalledWith("session-a", "baseline");
  });

  it("does not query Git for a Direct Workspace Session", async () => {
    renderHook(() => useGitReviewController({
      ready: true,
      session: {
        ...managedSession,
        id: "direct",
        project: {
          id: "project-direct",
          workspaceRoot: "/repository",
          gitAvailable: false,
        },
        worktree: undefined,
      },
    }));
    await act(async () => { await Promise.resolve(); });

    expect(window.eidosRuntime.readSessionGitStatus).not.toHaveBeenCalled();
    expect(window.eidosRuntime.readSessionGitDiff).not.toHaveBeenCalled();
  });

  it("loads local project branches for branch controls", async () => {
    const { result } = renderHook(() => useGitReviewController({
      ready: true,
      session: {
        ...managedSession,
        id: "local",
        executionMode: "local",
        worktree: undefined,
      },
    }));

    await waitFor(() => expect(result.current[0].projectContext?.branches).toEqual([
      "main", "dev-830-xl",
    ]));
    expect(window.eidosRuntime.readProjectGitContext).toHaveBeenCalledWith("/repository");
  });

  it("does not let a late response from the previous Session replace the selection", async () => {
    let resolveFirstStatus: ((status: SessionGitStatus) => void) | undefined;
    vi.mocked(window.eidosRuntime.readSessionGitStatus)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirstStatus = resolve; }))
      .mockResolvedValueOnce({ ...gitStatus, worktreeId: "worktree-b", branch: "eidos/b" });
    const { result, rerender } = renderHook(
      ({ session }: { session: Session }) => useGitReviewController({ ready: true, session }),
      { initialProps: { session: managedSession } },
    );
    rerender({
      session: {
        ...managedSession,
        id: "session-b",
        worktree: {
          ...managedSession.worktree!,
          worktreeId: "worktree-b",
          worktreeRoot: "/managed/b",
          branch: "eidos/b",
        },
      },
    });
    await waitFor(() => expect(result.current[0].status?.branch).toBe("eidos/b"));

    act(() => resolveFirstStatus?.(gitStatus));
    await act(async () => { await Promise.resolve(); });

    expect(result.current[0].status?.branch).toBe("eidos/b");
    expect(result.current[0].statusBySessionId.has("session-a")).toBe(false);
  });
});
