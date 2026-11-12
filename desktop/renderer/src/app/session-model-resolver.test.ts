import test from "node:test";
import assert from "node:assert/strict";
import type { Run } from "../contracts.js";
import { resolveSessionRun } from "./session-model-resolver.js";

function makeRun(partial: Partial<Run> & { id: string; sessionId: string; status: Run["status"] }): Run {
  return {
    modelId: "deepseek-v4-flash",
    modelStepCount: 1,
    createdAt: 1000,
    updatedAt: 1000,
    ...partial,
  };
}

test("resolveSessionRun returns undefined for empty run list", () => {
  assert.equal(resolveSessionRun([])?.modelId, undefined);
});

test("scheduled runs keep the user's model and approval defaults unchanged", () => {
  const user = makeRun({ id: "user", sessionId: "s", status: "succeeded", modelId: "deepseek-v4-pro" });
  const automatic = makeRun({ id: "automatic", sessionId: "s", status: "running", modelId: "deepseek-v4-flash", updatedAt: 2000,
    automationOrigin: { automationId: "task", occurrenceId: "occurrence", revision: 1, scheduledFor: "2026-10-15T09:00:00Z" } });
  assert.equal(resolveSessionRun([user, automatic])?.modelId, user.modelId);
  assert.equal(resolveSessionRun([automatic])?.modelId, undefined);
});

test("resolveSessionRun returns modelId for single run", () => {
  const run = makeRun({ id: "run-1", sessionId: "s-1", status: "succeeded", modelId: "deepseek-v4-pro" });
  assert.equal(resolveSessionRun([run])?.modelId, "deepseek-v4-pro");
});

test("resolveSessionRun prefers active run over newer terminal run", () => {
  const terminalNewer = makeRun({
    id: "run-1",
    sessionId: "s-1",
    status: "succeeded",
    modelId: "deepseek-v4-flash",
    updatedAt: 3000,
  });
  const activeOlder = makeRun({
    id: "run-2",
    sessionId: "s-1",
    status: "running",
    modelId: "deepseek-v4-pro",
    updatedAt: 2000,
  });

  assert.equal(resolveSessionRun([terminalNewer, activeOlder])?.modelId, "deepseek-v4-pro");
});

test("resolveSessionRun selects most recently updated terminal run when no active run exists", () => {
  const older = makeRun({
    id: "run-1",
    sessionId: "s-1",
    status: "succeeded",
    modelId: "deepseek-v4-flash",
    updatedAt: 1000,
  });
  const newer = makeRun({
    id: "run-2",
    sessionId: "s-1",
    status: "succeeded",
    modelId: "deepseek-v4-pro",
    updatedAt: 2000,
  });

  assert.equal(resolveSessionRun([older, newer])?.modelId, "deepseek-v4-pro");
});

test("resolveSessionRun produces deterministic result on equal timestamps", () => {
  const runA = makeRun({
    id: "run-a",
    sessionId: "s-1",
    status: "failed",
    modelId: "deepseek-v4-flash",
    createdAt: 1000,
    updatedAt: 1000,
  });
  const runB = makeRun({
    id: "run-b",
    sessionId: "s-1",
    status: "canceled",
    modelId: "deepseek-v4-pro",
    createdAt: 1000,
    updatedAt: 1000,
  });

  assert.equal(resolveSessionRun([runA, runB])?.modelId, "deepseek-v4-pro");
  assert.equal(resolveSessionRun([runB, runA])?.modelId, "deepseek-v4-pro");
});
