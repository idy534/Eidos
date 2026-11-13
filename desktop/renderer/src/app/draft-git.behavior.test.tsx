import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectGitContext, Session } from "../contracts.js";
import { useDraftGit } from "./useDraftGit.js";

const clean: ProjectGitContext = {
  gitAvailable: true,
  currentBranch: "topic",
  head: "first-commit",
  branches: ["main", "topic"],
  dirty: false,
  changedFileCount: 0,
  repositoryRoot: "/repository",
};

const draft: Session = {
  id: "draft-one",
  workspaceRoot: "/repository/subfolder",
  roots: [{ id: "primary", path: "/repository/subfolder", repositoryRoot: "/repository" }],
  project: { id: "project", workspaceRoot: "/repository/subfolder", gitAvailable: true },
  executionMode: "local",
  taskStatus: "new",
  createdAt: 1,
  updatedAt: 1,
};

const readProjectGitContext = vi.fn<() => Promise<ProjectGitContext>>();
const repositoryGit = vi.fn();

beforeEach(() => {
  readProjectGitContext.mockReset().mockResolvedValue(clean);
  repositoryGit.mockReset().mockResolvedValue({});
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readProjectGitContext, repositoryGit } });
});

describe("draft Git branch and workspace selection", () => {
  it("reads the saved project root and switches only on confirmation against the observed state", async () => {
    const { result } = renderHook(() => useDraftGit(draft, true));
    await waitFor(() => expect(result.current.view?.context?.currentBranch).toBe("topic"));
    expect(readProjectGitContext).toHaveBeenCalledWith({ projectId: "project", rootId: "primary" });

    act(() => result.current.view?.onSelectBranch("main"));
    expect(repositoryGit).not.toHaveBeenCalled();
    expect(result.current.requestedSwitch?.observed.repositoryRoot).toBe("/repository");
    act(() => result.current.cancelSwitch());
    expect(repositoryGit).not.toHaveBeenCalled();

    act(() => result.current.view?.onSelectBranch("main"));
    readProjectGitContext.mockResolvedValue({ ...clean, currentBranch: "main", head: "main-commit" });
    await act(async () => expect(await result.current.confirmSwitch()).toEqual({ status: "switched", branch: "main" }));
    expect(repositoryGit).toHaveBeenCalledWith({
      rootId: "primary", method: "session/gitSwitchBranch", parameters: {
        sessionId: "draft-one", projectId: "project", branch: "main", operationId: expect.any(String),
        expectedBranch: "topic", expectedHead: "first-commit",
      },
    });
    expect(result.current.view?.context?.currentBranch).toBe("main");
  });

  it("hands a rejected switch back to the caller instead of keeping it in the composer", async () => {
    const { result } = renderHook(() => useDraftGit(draft, true));
    await waitFor(() => expect(result.current.view?.context?.currentBranch).toBe("topic"));
    act(() => result.current.view?.onSelectBranch("main"));
    repositoryGit.mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:GIT_BRANCH_CHANGED"));
    await act(async () => expect(await result.current.confirmSwitch()).toEqual({
      status: "rejected", message: "本地分支或提交已变化。请确认当前状态后再切换。",
    }));
    expect(result.current.requestedSwitch).toBeUndefined();
    expect(result.current.switchError).toBeUndefined();
    expect(result.current.view?.error).toBeUndefined();
  });

  it("keeps a retryable switch failure in the open confirmation", async () => {
    const { result } = renderHook(() => useDraftGit(draft, true));
    await waitFor(() => expect(result.current.view?.context?.currentBranch).toBe("topic"));
    act(() => result.current.view?.onSelectBranch("main"));
    repositoryGit.mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:OPERATION_IN_PROGRESS"));
    await act(async () => expect(await result.current.confirmSwitch()).toBeUndefined());
    expect(result.current.requestedSwitch?.branch).toBe("main");
    expect(result.current.switchError).toBeTruthy();
    expect(result.current.view?.error).toBeUndefined();
  });

  it("permits starting from a dirty checkout but blocks switching it", async () => {
    readProjectGitContext.mockResolvedValue({ ...clean, dirty: true, changedFileCount: 1 });
    const { result } = renderHook(() => useDraftGit(draft, true));
    await waitFor(() => expect(result.current.view?.context?.dirty).toBe(true));
    act(() => result.current.view?.onSelectBranch("main"));
    expect(result.current.requestedSwitch).toBeUndefined();
    await act(async () => expect(await result.current.prepareSubmission()).toEqual({}));
    expect(repositoryGit).not.toHaveBeenCalled();
  });

  it("uses the exact worktree base and copies only an explicitly selected current snapshot", async () => {
    readProjectGitContext.mockResolvedValue({ ...clean, dirty: true, changedFileCount: 1, occupiedBranches: ["main"] });
    const { result } = renderHook(() => useDraftGit({ ...draft, executionMode: "worktree" }, true));
    await waitFor(() => expect(result.current.view?.canCopy).toBe(true));
    await act(async () => expect(await result.current.prepareSubmission()).toEqual({}));
    act(() => result.current.view?.onCopyChanges(true));
    await act(async () => expect(await result.current.prepareSubmission()).toEqual({ includeLocalChanges: true }));
    act(() => result.current.view?.onSelectBranch("main"));
    expect(result.current.view?.canCopy).toBe(false);
    await act(async () => expect(await result.current.prepareSubmission()).toEqual({ baseRef: "refs/heads/main" }));
    expect(repositoryGit).not.toHaveBeenCalled();
  });

  it("refreshes an externally changed local branch and asks the user to submit again", async () => {
    const { result } = renderHook(() => useDraftGit(draft, true));
    await waitFor(() => expect(result.current.view?.context?.currentBranch).toBe("topic"));
    readProjectGitContext.mockResolvedValue({ ...clean, currentBranch: "main", head: "main-commit" });
    await act(async () => {
      await expect(result.current.prepareSubmission()).rejects.toThrow("本地分支或提交已变化");
    });
    expect(result.current.view?.context?.currentBranch).toBe("main");
    await act(async () => expect(await result.current.prepareSubmission()).toEqual({}));
  });

  it("discards a previous primary root's branch selection and late observation", async () => {
    const first = { ...draft, executionMode: "worktree" as const };
    const { result, rerender } = renderHook(({ session }) => useDraftGit(session, true), { initialProps: { session: first } });
    await waitFor(() => expect(result.current.view?.context?.currentBranch).toBe("topic"));
    act(() => result.current.view?.onSelectBranch("main"));
    expect(result.current.view?.selectedBase).toBe("main");

    let resolveOld!: (value: ProjectGitContext) => void;
    readProjectGitContext.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    act(() => result.current.view?.onRefresh());
    readProjectGitContext.mockResolvedValue({ ...clean, currentBranch: "elsewhere" });
    rerender({ session: { ...first, roots: [{ id: "new-root", path: "/new-repository" }] } });
    await waitFor(() => expect(result.current.view?.context?.currentBranch).toBe("elsewhere"));
    await act(async () => resolveOld(clean));
    expect(result.current.view?.selectedBase).toBeUndefined();
    expect(result.current.view?.context?.currentBranch).toBe("elsewhere");
  });
});
