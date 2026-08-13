import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  GitMergeResult,
  GitRemoteStatus,
  ProjectGitContext,
  SessionGitCommitResult,
  SessionGitStatus,
} from "../contracts.js";
import { GitWorkflowControls } from "./GitWorkflowControls.js";


const status: SessionGitStatus = {
  worktreeId: "worktree-a",
  branch: "feature/review",
  head: "b".repeat(40),
  baseRef: "main",
  baseCommit: "a".repeat(40),
  dirty: true,
  stagedCount: 1,
  unstagedCount: 1,
  untrackedCount: 0,
  conflictCount: 0,
  stagedFiles: ["README.md"],
  unstagedFiles: ["src/index.ts"],
  untrackedFiles: [],
  conflictFiles: [],
  observedAt: 1,
};

const remote: GitRemoteStatus = {
  branch: "feature/review",
  remotes: [{ name: "origin" }],
  upstream: { remote: "origin", branch: "feature/review" },
  ahead: 2,
  behind: 1,
};

const branches: ProjectGitContext = {
  gitAvailable: true,
  currentBranch: "main",
  head: "b".repeat(40),
  branches: ["main", "feature/review", "release"],
  dirty: true,
  changedFileCount: 2,
};

function renderControls(overrides: Partial<Parameters<typeof GitWorkflowControls>[0]> = {}) {
  const readRemoteStatus = vi.fn().mockResolvedValue(remote);
  const readProjectGitContext = vi.fn().mockResolvedValue(branches);
  const commit = vi.fn().mockResolvedValue({ head: "c".repeat(40) } as SessionGitCommitResult);
  const fetch = vi.fn().mockResolvedValue({ ...remote, remote: "origin", head: status.head });
  const pull = vi.fn().mockResolvedValue({ ...remote, remote: "origin", head: status.head, status });
  const push = vi.fn().mockResolvedValue({ ...remote, remote: "origin", head: status.head, status });
  const merge = vi.fn().mockResolvedValue({
    head: status.head, branch: status.branch, status,
    operationState: "none", conflictFiles: [],
  } satisfies GitMergeResult);
  const mergeAbort = vi.fn().mockResolvedValue(undefined);
  const rebase = vi.fn().mockResolvedValue(undefined);
  const rebaseContinue = vi.fn().mockResolvedValue(undefined);
  const rebaseAbort = vi.fn().mockResolvedValue(undefined);
  const onRefresh = vi.fn();
  const result = render(
    <GitWorkflowControls
      sessionId="session-a"
      workspaceRoot="/workspace"
      status={status}
      disabled={false}
      onRefresh={onRefresh}
      readRemoteStatus={readRemoteStatus}
      readProjectGitContext={readProjectGitContext}
      commit={commit}
      fetch={fetch}
      pull={pull}
      push={push}
      merge={merge}
      mergeAbort={mergeAbort}
      rebase={rebase}
      rebaseContinue={rebaseContinue}
      rebaseAbort={rebaseAbort}
      {...overrides}
    />,
  );
  return {
    result,
    readRemoteStatus, readProjectGitContext, commit, fetch, pull, push,
    merge, mergeAbort, rebase, rebaseContinue, rebaseAbort, onRefresh,
  };
}

describe("GitWorkflowControls", () => {
  it("shows branch, upstream and ahead/behind observation", async () => {
    renderControls();

    expect(await screen.findByText("origin/feature/review")).toBeInTheDocument();
    expect(screen.getByText("↑2 ↓1")).toBeInTheDocument();
    expect(screen.getByText("feature/review")).toBeInTheDocument();
  });

  it("commits only the already staged changes", async () => {
    const { commit, onRefresh } = renderControls();
    fireEvent.change(screen.getByRole("textbox", { name: "Commit message" }), {
      target: { value: "Add review controls" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Commit" }));

    await waitFor(() => expect(commit).toHaveBeenCalledWith(
      "session-a", "Add review controls", expect.any(String),
    ));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("runs fetch, pull and push through typed APIs", async () => {
    const { fetch, pull, push } = renderControls();

    fireEvent.click(await screen.findByRole("button", { name: "Fetch" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("session-a", expect.any(String)));
    fireEvent.click(screen.getByRole("button", { name: "Pull" }));
    await waitFor(() => expect(pull).toHaveBeenCalledWith("session-a", expect.any(String)));
    fireEvent.click(screen.getByRole("button", { name: "Push" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("session-a", expect.any(String)));
  });

  it("creates a new operationId after a terminal Git failure", async () => {
    const fetch = vi.fn()
      .mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:GIT_REMOTE_FAILED"))
      .mockResolvedValueOnce({ ...remote, remote: "origin", head: status.head });
    renderControls({ fetch });

    fireEvent.click(await screen.findByRole("button", { name: "Fetch" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Fetch" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    expect(fetch.mock.calls[1]![1]).not.toBe(fetch.mock.calls[0]![1]);
  });

  it("keeps operationId while the operation is still in progress", async () => {
    const fetch = vi.fn()
      .mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:OPERATION_IN_PROGRESS"))
      .mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:OPERATION_IN_PROGRESS"));
    renderControls({ fetch });

    fireEvent.click(await screen.findByRole("button", { name: "Fetch" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Fetch" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    expect(fetch.mock.calls[1]![1]).toBe(fetch.mock.calls[0]![1]);
  });

  it("creates a new operationId after an uncertain outcome", async () => {
    const fetch = vi.fn()
      .mockRejectedValueOnce(new Error("EIDOS_RUNTIME_ERROR:GIT_REMOTE_OUTCOME_UNCERTAIN"))
      .mockResolvedValueOnce({ ...remote, remote: "origin", head: status.head });
    renderControls({ fetch });

    fireEvent.click(await screen.findByRole("button", { name: "Fetch" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Fetch" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    expect(fetch.mock.calls[1]![1]).not.toBe(fetch.mock.calls[0]![1]);
  });

  it("uses observed branches as merge and rebase targets", async () => {
    const { merge, rebase } = renderControls();
    const selector = await screen.findByRole("combobox", { name: "Git target" });
    expect(selector).toHaveTextContent("main");
    expect(selector).toHaveTextContent("release");
    expect(selector).not.toHaveTextContent("feature/review");

    fireEvent.change(selector, { target: { value: "release" } });
    fireEvent.click(screen.getByRole("button", { name: "Merge" }));
    await waitFor(() => expect(merge).toHaveBeenCalledWith(
      "session-a", "release", expect.any(String),
    ));
    fireEvent.click(screen.getByRole("button", { name: "Rebase" }));
    await waitFor(() => expect(rebase).toHaveBeenCalledWith(
      "session-a", "release", expect.any(String),
    ));
  });

  it("shows conflict continuation controls from the observed operation result", async () => {
    const conflict = {
      head: status.head,
      branch: status.branch,
      status: { ...status, conflictCount: 1, conflictFiles: ["conflict.txt"] },
      operationState: "rebase" as const,
      conflictFiles: ["conflict.txt"],
    };
    const rebase = vi.fn().mockResolvedValue(conflict);
    const rebaseContinue = vi.fn().mockResolvedValue({ ...conflict, operationState: "none" });
    const rebaseAbort = vi.fn().mockResolvedValue({ ...conflict, operationState: "none" });
    renderControls({ rebase, rebaseContinue, rebaseAbort });

    fireEvent.click(await screen.findByRole("button", { name: "Rebase" }));
    expect(await screen.findByText("conflict.txt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue Rebase" }));
    await waitFor(() => expect(rebaseContinue).toHaveBeenCalledWith(
      "session-a", expect.any(String),
    ));
  });

  it("offers native merge abort and rebase abort for conflict results", async () => {
    const mergeConflict = {
      head: status.head,
      branch: status.branch,
      status: { ...status, conflictCount: 1, conflictFiles: ["merge.txt"] },
      operationState: "merge" as const,
      conflictFiles: ["merge.txt"],
    };
    const merge = vi.fn().mockResolvedValue(mergeConflict);
    const mergeAbort = vi.fn().mockResolvedValue({ ...mergeConflict, operationState: "none" });
    const { result } = renderControls({ merge, mergeAbort });

    fireEvent.click(await screen.findByRole("button", { name: "Merge" }));
    fireEvent.click(await screen.findByRole("button", { name: "Abort Merge" }));
    await waitFor(() => expect(mergeAbort).toHaveBeenCalledWith(
      "session-a", expect.any(String),
    ));

    result.unmount();
    const rebaseConflict = { ...mergeConflict, operationState: "rebase" as const };
    const rebase = vi.fn().mockResolvedValue(rebaseConflict);
    const rebaseAbort = vi.fn().mockResolvedValue({ ...rebaseConflict, operationState: "none" });
    renderControls({ rebase, rebaseAbort });
    fireEvent.click(await screen.findByRole("button", { name: "Rebase" }));
    fireEvent.click(await screen.findByRole("button", { name: "Abort Rebase" }));
    await waitFor(() => expect(rebaseAbort).toHaveBeenCalledWith(
      "session-a", expect.any(String),
    ));
  });

  it("offers Create Branch Here for detached managed worktrees", async () => {
    const onCreateBranch = vi.fn();
    renderControls({ status: { ...status, branch: null }, onCreateBranch });

    fireEvent.click(await screen.findByRole("button", { name: "Create Branch Here" }));
    expect(onCreateBranch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled();
  });

  it("disables conflicting actions while an Agent Run is active", async () => {
    renderControls({ disabled: true });

    expect(await screen.findByRole("button", { name: "Fetch" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pull" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Push" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Merge" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Rebase" })).toBeDisabled();
  });
});
