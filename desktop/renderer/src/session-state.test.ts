import assert from "node:assert/strict";
import test from "node:test";

import type { Item, Run, RuntimeNotification, SessionSnapshot } from "./contracts.js";
import {
  applyNotification,
  groupSessionsByWorkspace,
  SnapshotReadCoordinator,
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
    { session, runs: [], items: [previousUser, previousAssistant] },
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
    label: "Run 已完成",
    tone: "success",
  });
  assert.deepEqual(terminalRunPresentation(run("failed")), {
    label: "Run 失败：UNKNOWN_ERROR",
    tone: "error",
  });
  assert.deepEqual(terminalRunPresentation(run("canceled")), {
    label: "Run 已取消",
    tone: "neutral",
  });
  assert.deepEqual(terminalRunPresentation(run("interrupted")), {
    label: "Run 已中断，未自动恢复",
    tone: "warning",
  });
  assert.equal(terminalRunPresentation(run("running")), undefined);
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
    userFacingError(new Error("provider secret details")),
    "操作失败，请查看 Runtime 日志。",
  );
});

test("projects keep creation order while tasks stay newest first", () => {
  const groups = groupSessionsByWorkspace([
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

  assert.deepEqual(groups.map((group) => group.workspaceRoot), ["/new", "/old"]);
  assert.deepEqual(groups[1]?.sessions.map((item) => item.title), ["第二期规划", "分析架构"]);
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
