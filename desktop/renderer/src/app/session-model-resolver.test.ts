import test from "node:test";
import assert from "node:assert/strict";
import type { Run } from "../contracts.js";
import { resolveSessionModelId } from "./session-model-resolver.js";

function makeRun(partial: Partial<Run> & { id: string; sessionId: string; status: Run["status"] }): Run {
  return {
    modelId: "deepseek-v4-flash",
    modelStepCount: 1,
    createdAt: 1000,
    updatedAt: 1000,
    ...partial,
  };
}

test("resolveSessionModelId returns undefined for empty run list", () => {
  assert.equal(resolveSessionModelId([]), undefined);
});

test("resolveSessionModelId returns modelId for single run", () => {
  const run = makeRun({ id: "run-1", sessionId: "s-1", status: "succeeded", modelId: "deepseek-v4-pro" });
  assert.equal(resolveSessionModelId([run]), "deepseek-v4-pro");
});

test("resolveSessionModelId prefers active run over newer terminal run", () => {
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

  assert.equal(resolveSessionModelId([terminalNewer, activeOlder]), "deepseek-v4-pro");
});

test("resolveSessionModelId selects most recently updated terminal run when no active run exists", () => {
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

  assert.equal(resolveSessionModelId([older, newer]), "deepseek-v4-pro");
});

test("resolveSessionModelId produces deterministic result on equal timestamps", () => {
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

  assert.equal(resolveSessionModelId([runA, runB]), "deepseek-v4-pro");
  assert.equal(resolveSessionModelId([runB, runA]), "deepseek-v4-pro");
});
