import assert from "node:assert/strict";
import test from "node:test";

import type { Item, Run, RuntimeNotification, SessionSnapshot } from "./contracts.js";
import {
  applyNotification,
  groupSessionsByProject,
  SnapshotReadCoordinator,
  taskStatusFromRun,
  taskStatusPresentation,
  terminalRunPresentation,
  userFacingError,
} from "./session-state.js";


const session = {
  id: "session-1",
  workspaceRoot: "/workspace",
  taskStatus: "new" as const,
  createdAt: 1,
  updatedAt: 1,
};

function run(status: Run["status"]): Run {
  return {
    id: "run-1",
    sessionId: session.id,
    userInput: "Inspect the project",
    status,
    modelId: "deepseek-v4-flash",
    modelStepCount: status === "running" ? 0 : 1,
    createdAt: 1,
    startedAt: 1,
    updatedAt: status === "running" ? 1 : 2,
    ...(status === "running" ? {} : { completedAt: 2 }),
  };
}

function assistant(content: string, status: Item["status"]): Item {
  return {
    id: "item-1",
    sessionId: session.id,
    runId: "run-1",
    ordinal: 2,
    kind: "assistant_message",
    status,
    createdAt: 1,
    content,
    ...(status === "in_progress" ? {} : { completedAt: 2 }),
  };
}

function snapshot(status: Run["status"], content: string): SessionSnapshot {
  return {
    session,
    runs: [run(status)],
    items: [assistant(content, status === "running" ? "in_progress" : "completed")],
    stepResolutions: [],
  };
}

test("a completion refresh supersedes an older in-flight snapshot read", () => {
  const reads = new SnapshotReadCoordinator();
  const initialRead = reads.select(session.id);
  const completionRefresh = reads.refresh(session.id);

  assert.ok(completionRefresh);
  assert.equal(reads.accept(initialRead, snapshot("running", "Done.")), undefined);

  const authoritative = snapshot("succeeded", "Done.");
  assert.equal(reads.accept(completionRefresh, authoritative), authoritative);
  assert.equal(authoritative.items[0]?.content, "Done.");
});

test("a snapshot below the accepted event waterline is rejected", () => {
  const reads = new SnapshotReadCoordinator();
  const current = reads.select(session.id);
  assert.ok(reads.accept(current, { ...snapshot("running", "new"), throughEventId: 8 }));
  const stale = reads.refresh(session.id);
  assert.ok(stale);
  assert.equal(
    reads.accept(stale, { ...snapshot("running", "old"), throughEventId: 7 }),
    undefined,
  );
});

test("a committed background title update refreshes the open session", () => {
  const result = applyNotification(snapshot("running", "working"), {
    method: "session/titleUpdated",
    params: { sessionId: session.id, title: "后台标题" },
  });

  assert.equal(result?.session.title, "后台标题");
});

test("approval lifecycle notifications do not mutate session facts", () => {
  const current = snapshot("running", "working");
  const notification: RuntimeNotification = {
    method: "approval/canceled",
    params: {
      sessionId: session.id,
      runId: "run-1",
      approvalId: "approval-1",
      status: "canceled",
    },
  };

  assert.equal(applyNotification(current, notification), current);
});

test("a completed assistant item without content preserves streamed text", () => {
  const streaming = snapshot("running", "Done.");
  const completedItem = assistant("", "completed");
  delete completedItem.content;
  const notification: RuntimeNotification = {
    method: "item/completed",
    params: {
      sessionId: session.id,
      runId: "run-1",
      item: completedItem,
    },
  };

  const result = applyNotification(streaming, notification);

  assert.equal(result?.items[0]?.content, "Done.");
  assert.equal(result?.items[0]?.status, "completed");
});

test("a new run's user item stays after the preceding run's items", () => {
  const previousUser: Item = {
    id: "item-previous-user",
    sessionId: session.id,
    runId: "run-previous",
    ordinal: 1,
    kind: "user_message",
    status: "completed",
    createdAt: 1,
    content: "First question",
    completedAt: 1,
  };
  const previousAssistant: Item = {
    ...assistant("First answer", "completed"),
    id: "item-previous-assistant",
    runId: "run-previous",
  };
  const newUser: Item = {
    ...previousUser,
    id: "item-new-user",
    runId: "run-new",
    content: "Second question",
  };
  const result = applyNotification(
    {
      session,
      runs: [],
      items: [previousUser, previousAssistant],
      stepResolutions: [],
    },
    {
      method: "item/started",
      params: { sessionId: session.id, runId: newUser.runId, item: newUser },
    },
  );

  assert.deepEqual(result?.items.map((item) => item.id), [
    previousUser.id,
    previousAssistant.id,
    newUser.id,
  ]);
});

test("run completion replaces the active run with its terminal state", () => {
  const notification: RuntimeNotification = {
    method: "run/completed",
    params: { sessionId: session.id, run: run("canceled") },
  };

  const result = applyNotification(snapshot("running", "partial"), notification);

  assert.equal(result?.runs[0]?.status, "canceled");
});

test("every terminal run state has a user-facing presentation", () => {
  assert.deepEqual(terminalRunPresentation(run("succeeded")), {
    label: "已完成",
    tone: "success",
  });
  assert.deepEqual(terminalRunPresentation(run("failed")), {
    label: "失败：UNKNOWN_ERROR",
    tone: "error",
  });
  assert.deepEqual(terminalRunPresentation(run("canceled")), {
    label: "已取消",
    tone: "neutral",
  });
  assert.deepEqual(terminalRunPresentation(run("interrupted")), {
    label: "已中断，未自动恢复",
    tone: "warning",
  });
  assert.deepEqual(
    terminalRunPresentation({ ...run("stopped"), stopReason: "repeated_tool_call" }),
    {
      label: "检测到重复工具调用，任务已停止",
      tone: "warning",
    },
  );
  assert.deepEqual(
    terminalRunPresentation({ ...run("stopped"), stopReason: "max_effective_runtime" }),
    {
      label: "已达到最长执行时间",
      tone: "warning",
    },
  );
  assert.equal(terminalRunPresentation(run("running")), undefined);
});

test("does not present a succeeded run with an unresolved reconciliation barrier as complete", () => {
  const unresolved = {
    ...run("succeeded"),
    reconciliationRequired: true,
  };

  assert.deepEqual(terminalRunPresentation(unresolved), {
    label: "完成状态待核验，尚未确认",
    tone: "warning",
  });
  assert.equal(taskStatusFromRun(unresolved), "failed");
});

test("maps a completed run notification with a reconciliation barrier to a failed session", () => {
  assert.equal(
    taskStatusFromRun({ status: "succeeded", reconciliationRequired: true }),
    "failed",
  );
});

test("closed runtime business errors map to safe user-facing guidance", () => {
  assert.equal(
    userFacingError(
      new Error(
        "Error invoking remote method 'run:start': Error: EIDOS_RUNTIME_ERROR:RUN_ALREADY_ACTIVE",
      ),
    ),
    "当前已有一个 Run 正在执行，请先等待完成或取消。",
  );
  assert.equal(
    userFacingError(
      new Error("EIDOS_RUNTIME_ERROR:WORKSPACE_IDENTITY_CHANGED"),
    ),
    "任务目录的身份已经变化，Run 未启动。请刷新后重试。",
  );
  assert.equal(
    userFacingError(new Error("EIDOS_RUNTIME_ERROR:WORKTREE_DIRTY")),
    "任务仍有未提交或冲突的变更，不能删除。",
  );
  assert.equal(
    userFacingError(new Error("EIDOS_RUNTIME_ERROR:REVIEW_ANCHOR_INVALID")),
    "Review Comment 的行位置已无效，请重新选择 Diff 行。",
  );
  assert.equal(
    userFacingError(new Error("EIDOS_RUNTIME_ERROR:GIT_REMOTE_OUTCOME_UNCERTAIN")),
    "上一次 Git 操作可能已产生外部变更。请先刷新并检查 Git/远端状态；再次执行将作为新的 Git 操作。",
  );
  assert.equal(
    userFacingError(new Error("provider secret details")),
    "操作失败，请查看 Runtime 日志。",
  );
  assert.equal(
    userFacingError(new Error("EIDOS_RUNTIME_ERROR:PROJECT_HAS_SESSIONS")),
    "项目下还有任务，请先删除任务后再删除项目。",
  );
  assert.equal(
    userFacingError(new Error("EIDOS_RUNTIME_ERROR:HANDOFF_LOCAL_CONFLICT")),
    "当前工作树的修改无法安全同步到本地。请先处理本地冲突。",
  );
  assert.equal(
    userFacingError(new Error("EIDOS_RUNTIME_ERROR:HANDOFF_SOURCE_CHANGED")),
    "当前工作环境已经发生变化。请刷新后重试。",
  );
});

test("groups sort by most recent session activity, tasks stay newest first", () => {
  const groups = groupSessionsByProject([
    { ...session, id: "session-2", workspaceRoot: "/old", title: "第二期规划", createdAt: 10, updatedAt: 10 },
    {
      ...session,
      id: "session-3",
      workspaceRoot: "/new",
      title: "修复测试",
      createdAt: 5,
      updatedAt: 5,
    },
    { ...session, id: "session-1", workspaceRoot: "/old", title: "分析架构", createdAt: 1, updatedAt: 1 },
  ]);

  // /new earliest session (createdAt=5) > /old earliest session (createdAt=1), so /new sorts first
  assert.deepEqual(groups.map((group) => group.workspaceRoot), ["/new", "/old"]);
  assert.deepEqual(groups[0]?.sessions.map((item) => item.title), ["修复测试"]);
  assert.deepEqual(groups[1]?.sessions.map((item) => item.title), ["第二期规划", "分析架构"]);
});

test("projectless group is always pinned to the bottom", () => {
  const groups = groupSessionsByProject([
    { ...session, id: "p-session", workspaceRoot: "/project-ws", createdAt: 1, updatedAt: 1 },
    { ...session, id: "recent-a", projectless: true, workspaceRoot: "/chat/a", createdAt: 999, updatedAt: 999 },
  ]);

  // Even if projectless sessions are very recent, the group stays last
  assert.equal(groups.at(-1)?.key, "projectless");
  assert.equal(groups.at(-1)?.displayName, "最近");
});

test("direct and managed threads group by explicit Project identity", () => {
  const managedWorktree = {
    worktreeId: "worktree-a",
    projectId: "project-a",
    repositoryRoot: "/repository",
    worktreeRoot: "/managed/a",
    baseRef: "main",
    baseCommit: "a".repeat(40),
    branch: "eidos/a",
    state: "active" as const,
  };
  const groups = groupSessionsByProject([
    {
      ...session,
      id: "managed-a",
      createdAt: 3,
      project: { id: "project-a", workspaceRoot: "/repository", gitAvailable: true },
      worktree: managedWorktree,
    },
    {
      ...session,
      id: "managed-b",
      createdAt: 2,
      project: { id: "project-a", workspaceRoot: "/repository", gitAvailable: true },
      worktree: {
        ...managedWorktree,
        worktreeId: "worktree-b",
        worktreeRoot: "/managed/b",
        branch: "eidos/b",
      },
    },
    {
      ...session,
      id: "direct-same-path",
      workspaceRoot: "/repository",
      project: { id: "project-direct", workspaceRoot: "/repository", gitAvailable: false },
      createdAt: 4,
    },
    {
      ...session,
      id: "direct-other",
      workspaceRoot: "/legacy",
      project: { id: "project-other", workspaceRoot: "/legacy", gitAvailable: false },
      createdAt: 1,
    },
  ]);

  assert.equal(groups.length, 3);
  assert.deepEqual(groups.map((group) => group.key), [
    "project-direct",
    "project-a",
    "project-other",
  ]);
  assert.deepEqual(
    groups.find((group) => group.key === "project-a")?.sessions.map((item) => item.id),
    ["managed-a", "managed-b"],
  );
  assert.equal(groups.find((group) => group.key === "project-a")?.gitAvailable, true);
  assert.equal(groups.find((group) => group.key === "project-direct")?.gitAvailable, false);
  assert.equal(groups.find((group) => group.key === "project-direct")?.displayName, "repository");
});

test("projectless sessions share the recent group", () => {
  const groups = groupSessionsByProject([
    { ...session, id: "chat-a", projectless: true, workspaceRoot: "/private/chat/a", createdAt: 3 },
    { ...session, id: "chat-b", projectless: true, workspaceRoot: "/private/chat/b", createdAt: 2 },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0]?.key, "projectless");
  assert.equal(groups[0]?.displayName, "最近");
  assert.equal(groups[0]?.projectId, undefined);
});

test("empty projects remain visible after their sessions are deleted", () => {
  const groups = groupSessionsByProject([], [
    {
      id: "project-empty",
      workspaceRoot: "/empty-project",
      gitAvailable: false,
      createdAt: 1,
      updatedAt: 1,
    },
  ]);

  assert.deepEqual(groups.map((group) => group.key), ["project-empty"]);
  assert.deepEqual(groups[0]?.sessions, []);
  assert.equal(groups[0]?.displayName, "empty-project");
});

test("task statuses use compact accessible indicators", () => {
  assert.deepEqual(taskStatusPresentation("completed"), {
    label: "未读完成",
    tone: "success",
    spinning: false,
  });
  assert.equal(taskStatusPresentation("completed", true), undefined);
  assert.deepEqual(taskStatusPresentation("in_progress"), {
    label: "进行中",
    tone: "progress",
    spinning: true,
  });
  assert.deepEqual(taskStatusPresentation("failed"), {
    label: "失败",
    tone: "error",
    spinning: false,
  });
  assert.equal(taskStatusPresentation("new"), undefined);
  assert.equal(taskStatusPresentation("canceled"), undefined);
});
