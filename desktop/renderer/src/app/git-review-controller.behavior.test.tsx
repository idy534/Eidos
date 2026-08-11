import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  EidosRuntimeAPI,
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
  observedAt: 1,
};

function gitDiff(scope: "head" | "baseline"): SessionGitDiff {
  return {
    scope,
    baseCommit: "a".repeat(40),
    head: "b".repeat(40),
    dirty: true,
    changedFiles: ["README.md"],
    unifiedDiff: "diff --git a/README.md b/README.md\n",
    truncated: false,
    observedAt: 1,
  };
}

const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

describe("useGitReviewController", () => {
  beforeEach(() => {
    vi.useRealTimers();
    const api: Partial<EidosRuntimeAPI> = {
      readSessionGitStatus: vi.fn().mockResolvedValue(gitStatus),
      readSessionGitDiff: vi.fn().mockImplementation((_sessionId, scope) => (
        Promise.resolve(gitDiff(scope))
      )),
    };
    (window as unknown as { eidosRuntime: EidosRuntimeAPI }).eidosRuntime = api as EidosRuntimeAPI;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
    else delete (window as Partial<Window>).eidosRuntime;
  });

  it("loads status and the default HEAD Diff for a selected managed Thread", async () => {
    const { result } = renderHook(() => useGitReviewController({
      ready: true,
      session: managedSession,
    }));

    await waitFor(() => expect(result.current[0].diff?.scope).toBe("head"));

    expect(window.eidosRuntime.readSessionGitStatus).toHaveBeenCalledWith("session-a");
    expect(window.eidosRuntime.readSessionGitDiff).toHaveBeenCalledWith("session-a", "head");
    expect(result.current[0].status?.dirty).toBe(true);
  });

  it("switches to Baseline Diff without needlessly reloading status", async () => {
    const { result } = renderHook(() => useGitReviewController({
      ready: true,
      session: managedSession,
    }));
    await waitFor(() => expect(result.current[0].diff).toBeDefined());
    vi.mocked(window.eidosRuntime.readSessionGitStatus).mockClear();
    vi.mocked(window.eidosRuntime.readSessionGitDiff).mockClear();

    act(() => result.current[1].selectScope("baseline"));
    await waitFor(() => expect(result.current[0].diff?.scope).toBe("baseline"));

    expect(window.eidosRuntime.readSessionGitStatus).not.toHaveBeenCalled();
    expect(window.eidosRuntime.readSessionGitDiff).toHaveBeenCalledOnce();
    expect(window.eidosRuntime.readSessionGitDiff).toHaveBeenCalledWith(
      "session-a", "baseline",
    );
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
    expect(window.eidosRuntime.readSessionGitDiff).toHaveBeenCalledOnce();
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

  it("does not let a late response from the previous Session replace the selection", async () => {
    let resolveFirstStatus: ((status: SessionGitStatus) => void) | undefined;
    vi.mocked(window.eidosRuntime.readSessionGitStatus)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirstStatus = resolve; }))
      .mockResolvedValueOnce({ ...gitStatus, worktreeId: "worktree-b", branch: "eidos/b" });
    vi.mocked(window.eidosRuntime.readSessionGitDiff)
      .mockImplementationOnce(() => new Promise(() => undefined))
      .mockResolvedValueOnce({ ...gitDiff("head"), head: "c".repeat(40) });

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
