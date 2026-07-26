import assert from "node:assert/strict";
import test from "node:test";

import type { Run } from "./contracts.js";
import {
  deriveComposerMode,
  findActiveRun,
} from "./session-state.js";

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function run(status: Run["status"], overrides?: Partial<Run>): Run {
  return {
    id: "run-1",
    sessionId: "session-1",
    status,
    modelId: "deepseek-v4-flash",
    modelStepCount: 1,
    createdAt: 1,
    startedAt: 1,
    updatedAt: 1,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// deriveComposerMode
// ---------------------------------------------------------------------------

test("read_only when storage is not healthy, regardless of run state", () => {
  assert.equal(deriveComposerMode(false, undefined, false), "read_only");
  assert.equal(deriveComposerMode(false, run("running"), false), "read_only");
  assert.equal(deriveComposerMode(false, run("idle" as Run["status"]), true), "read_only");
});

test("starting when isStarting=true and no active run yet", () => {
  assert.equal(deriveComposerMode(true, undefined, true), "starting");
});

test("running when isStarting=true AND active run already exists (run appeared faster than IPC returned)", () => {
  // If startRun returns before we even receive the run/started notification,
  // we might momentarily have isStarting=true and an active run. We should
  // show running (not starting) to avoid double-loading.
  assert.equal(deriveComposerMode(true, run("running"), true), "running");
  assert.equal(deriveComposerMode(true, run("queued"), true), "running");
});

test("idle when storage ready, no active run, not starting", () => {
  assert.equal(deriveComposerMode(true, undefined, false), "idle");
});

test("idle after terminal run (terminal run should not be passed as activeRun)", () => {
  // Caller is responsible for not passing terminal runs
  // (findActiveRun filters them out). We test the whole pipeline.
  const runs = [run("succeeded")];
  assert.equal(findActiveRun(runs), undefined);
  assert.equal(deriveComposerMode(true, findActiveRun(runs), false), "idle");
});

test("running when run is queued or running", () => {
  assert.equal(deriveComposerMode(true, run("queued"), false), "running");
  assert.equal(deriveComposerMode(true, run("running"), false), "running");
});

test("waiting_approval when run is waiting_approval", () => {
  assert.equal(deriveComposerMode(true, run("waiting_approval"), false), "waiting_approval");
});

test("waiting_user_input when run is waiting_user_input", () => {
  assert.equal(deriveComposerMode(true, run("waiting_user_input"), false), "waiting_user_input");
});

test("finalizing when run is finalizing", () => {
  assert.equal(deriveComposerMode(true, run("finalizing"), false), "finalizing");
});

// ---------------------------------------------------------------------------
// findActiveRun
// ---------------------------------------------------------------------------

test("returns undefined for empty run list", () => {
  assert.equal(findActiveRun([]), undefined);
});

test("returns undefined when only terminal runs exist", () => {
  const runs = [run("succeeded"), run("failed"), run("canceled"), run("interrupted"), run("stopped")];
  assert.equal(findActiveRun(runs), undefined);
});

test("returns the most recent active run", () => {
  const first = run("running", { id: "run-1", updatedAt: 1 });
  const second = run("queued", { id: "run-2", updatedAt: 2 });
  // findActiveRun uses array order (last wins), not updatedAt
  const result = findActiveRun([first, second]);
  assert.equal(result?.id, "run-2");
});

test("active run is preferrred over a terminal run", () => {
  const terminal = run("succeeded", { id: "run-1" });
  const active = run("running", { id: "run-2" });
  const result = findActiveRun([terminal, active]);
  assert.equal(result?.id, "run-2");
});

test("finds waiting_user_input run", () => {
  const paused = run("waiting_user_input", { id: "paused" });
  assert.equal(findActiveRun([paused])?.id, "paused");
});

test("finds waiting_approval run", () => {
  const approving = run("waiting_approval", { id: "approving" });
  assert.equal(findActiveRun([approving])?.id, "approving");
});

test("finds finalizing run", () => {
  const finalizing = run("finalizing", { id: "finalizing" });
  assert.equal(findActiveRun([finalizing])?.id, "finalizing");
});

// ---------------------------------------------------------------------------
// Transition table completeness
// ---------------------------------------------------------------------------

const ALL_RUN_STATUSES: Run["status"][] = [
  "queued", "running", "waiting_approval", "waiting_user_input", "finalizing",
  "stopped", "succeeded", "failed", "canceled", "interrupted",
];

const ACTIVE_STATUSES: Run["status"][] = [
  "queued", "running", "waiting_approval", "waiting_user_input", "finalizing",
];

const TERMINAL_STATUSES: Run["status"][] = [
  "stopped", "succeeded", "failed", "canceled", "interrupted",
];

test("deriveComposerMode returns a value for all run statuses", () => {
  for (const status of ALL_RUN_STATUSES) {
    const mode = deriveComposerMode(true, run(status), false);
    assert.ok(mode, `Expected a mode for status "${status}"`);
  }
});

test("findActiveRun finds all active run statuses", () => {
  for (const status of ACTIVE_STATUSES) {
    const activeRun = findActiveRun([run(status)]);
    assert.ok(activeRun, `Expected findActiveRun to find run with status "${status}"`);
  }
});

test("findActiveRun returns undefined for all terminal statuses", () => {
  for (const status of TERMINAL_STATUSES) {
    const activeRun = findActiveRun([run(status)]);
    assert.equal(activeRun, undefined, `Expected findActiveRun to return undefined for status "${status}"`);
  }
});
