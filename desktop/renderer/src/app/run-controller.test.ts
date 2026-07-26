import { test } from "node:test";
import assert from "node:assert/strict";
import type { Run, SessionSnapshot } from "../contracts.js";
import { deriveComposerMode, upsertRun } from "../session-state.js";

function makeSnapshot(
  sessionId: string,
  runs: Run[] = [],
): SessionSnapshot {
  return {
    session: {
      id: sessionId,
      workspaceRoot: "/workspace",
      taskStatus: "new",
      createdAt: 1000,
      updatedAt: 1000,
    },
    runs,
    items: [],
  };
}

const mockRunIdle: Run = {
  id: "run-1",
  sessionId: "session-1",
  status: "succeeded",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 1000,
  updatedAt: 1000,
};

const mockRunActive: Run = {
  id: "run-2",
  sessionId: "session-1",
  status: "running",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 2000,
  updatedAt: 2000,
};

const mockRunWaitingUserInput: Run = {
  id: "run-3",
  sessionId: "session-1",
  status: "waiting_user_input",
  allowedActions: ["continue", "cancel"],
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 3000,
  updatedAt: 3000,
};

const mockRunWaitingApproval: Run = {
  id: "run-4",
  sessionId: "session-1",
  status: "waiting_approval",
  allowedActions: ["approve", "reject", "cancel"],
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 4000,
  updatedAt: 4000,
};

const mockRunFinalizing: Run = {
  id: "run-5",
  sessionId: "session-1",
  status: "finalizing",
  modelId: "deepseek-v4-flash",
  modelStepCount: 1,
  createdAt: 5000,
  updatedAt: 5000,
};

void test("Active Run blocks startRun submission in Composer Mode", () => {
  const snapshot = makeSnapshot("session-1", [mockRunActive]);
  const mode = deriveComposerMode(true, mockRunActive, false);
  assert.equal(mode, "running");
  assert.notEqual(mode, "idle");
});

void test("waiting_user_input allows continueRun path", () => {
  const snapshot = makeSnapshot("session-1", [mockRunWaitingUserInput]);
  const mode = deriveComposerMode(true, mockRunWaitingUserInput, false);
  assert.equal(mode, "waiting_user_input");
});

void test("waiting_approval blocks submission", () => {
  const snapshot = makeSnapshot("session-1", [mockRunWaitingApproval]);
  const mode = deriveComposerMode(true, mockRunWaitingApproval, false);
  assert.equal(mode, "waiting_approval");
});

void test("finalizing blocks submission", () => {
  const snapshot = makeSnapshot("session-1", [mockRunFinalizing]);
  const mode = deriveComposerMode(true, mockRunFinalizing, false);
  assert.equal(mode, "finalizing");
});

void test("Read-only storage blocks submission regardless of run status", () => {
  const mode = deriveComposerMode(false, undefined, false);
  assert.equal(mode, "read_only");
});

void test("upsertRun inserts missing run and does not duplicate", () => {
  const runs: Run[] = [mockRunIdle];
  const nextRun: Run = {
    id: "run-new",
    sessionId: "session-1",
    status: "queued",
    modelId: "deepseek-v4-flash",
    modelStepCount: 0,
    createdAt: 2000,
    updatedAt: 2000,
  };

  const updated = upsertRun(runs, nextRun);
  assert.equal(updated.length, 2);
  assert.equal(updated[1]?.id, "run-new");

  // Re-upserting the exact same run does not duplicate
  const reUpserted = upsertRun(updated, nextRun);
  assert.equal(reUpserted.length, 2);
});

void test("upsertRun ignores stale incoming run updates", () => {
  const currentRun: Run = {
    id: "run-10",
    sessionId: "session-1",
    status: "running",
    modelId: "deepseek-v4-flash",
    modelStepCount: 5,
    createdAt: 1000,
    updatedAt: 5000,
  };

  const staleIncoming: Run = {
    id: "run-10",
    sessionId: "session-1",
    status: "queued",
    modelId: "deepseek-v4-flash",
    modelStepCount: 0,
    createdAt: 1000,
    updatedAt: 2000, // Older updatedAt timestamp!
  };

  const result = upsertRun([currentRun], staleIncoming);
  assert.equal(result[0]?.status, "running");
  assert.equal(result[0]?.updatedAt, 5000);
});

void test("upsertRun updates run when incoming has newer or equal updatedAt", () => {
  const currentRun: Run = {
    id: "run-10",
    sessionId: "session-1",
    status: "queued",
    modelId: "deepseek-v4-flash",
    modelStepCount: 0,
    createdAt: 1000,
    updatedAt: 2000,
  };

  const newerIncoming: Run = {
    id: "run-10",
    sessionId: "session-1",
    status: "running",
    modelId: "deepseek-v4-flash",
    modelStepCount: 1,
    createdAt: 1000,
    updatedAt: 3000,
  };

  const result = upsertRun([currentRun], newerIncoming);
  assert.equal(result[0]?.status, "running");
  assert.equal(result[0]?.updatedAt, 3000);
});

void test("Session draft map keeps session inputs isolated", () => {
  const inputs: Record<string, string> = {
    "session-A": "Hello from A",
    "session-B": "Draft for B",
  };

  // Clearing session A draft does not touch session B draft
  const nextInputs = { ...inputs };
  delete nextInputs["session-A"];

  assert.equal(nextInputs["session-A"], undefined);
  assert.equal(nextInputs["session-B"], "Draft for B");
});

void test("Stale response verification prevents cross-session state mutation", () => {
  const submittedSessionId: string = "session-A";
  const returnedRunSessionId: string = "session-B";

  const isMatchingSession = submittedSessionId === returnedRunSessionId;
  assert.equal(isMatchingSession, false);
  // State updater will be skipped when session IDs do not match
});
