import { test } from "node:test";
import assert from "node:assert/strict";
import type { EidosRuntimeAPI } from "./ipc-api.js";
import { IPC } from "./ipc-channels.js";
import { MAX_APPROVAL_FEEDBACK_BYTES } from "./constants.js";
import type { ModelId, RuntimeStatus } from "./domain-contracts.js";

interface TestWindow {
  eidosRuntime: EidosRuntimeAPI;
}

/** Compile-time type check: EidosRuntimeAPI must match TestWindow['eidosRuntime'] */
type WindowEidosRuntimeMatches = TestWindow["eidosRuntime"] extends EidosRuntimeAPI
  ? EidosRuntimeAPI extends TestWindow["eidosRuntime"]
    ? true
    : false
  : false;

const _assertWindowApiMatches: WindowEidosRuntimeMatches = true;
type TestConnectionIsNotExposed = "testModelProfile" extends keyof EidosRuntimeAPI
  ? true
  : false;
const _assertTestConnectionIsNotExposed: TestConnectionIsNotExposed = false;
const gitDiffArguments: Parameters<EidosRuntimeAPI["readSessionGitDiff"]> = [
  "session-id",
  "baseline",
  undefined,
  "origin/main",
];

void test("IPC channel object provides central authoritative channels", () => {
  assert.equal(IPC.RUNTIME_GET_STATUS, "runtime:get-status");
  assert.equal(IPC.RUNTIME_HEALTH, "runtime:health");
  assert.equal(IPC.RUN_START, "run:start");
  assert.equal(IPC.RUN_CANCEL, "run:cancel");
  assert.equal(IPC.RUN_REVISE, "run:revise");
  assert.equal(IPC.CONTEXT_USAGE, "context:usage");
  assert.equal(IPC.SESSION_GIT_STATUS, "session:git-status");
  assert.equal(IPC.SESSION_GIT_DIFF, "session:git-diff");
  assert.equal(IPC.SESSION_GIT_MERGE, "session:git-merge");
  assert.equal(IPC.SESSION_GIT_MERGE_ABORT, "session:git-merge-abort");
  assert.equal(IPC.SESSION_GIT_REBASE, "session:git-rebase");
  assert.equal(IPC.SESSION_GIT_REBASE_CONTINUE, "session:git-rebase-continue");
  assert.equal(IPC.SESSION_GIT_REBASE_ABORT, "session:git-rebase-abort");
  assert.equal(IPC.RESPONSE_ACTION_STATE, "response-action:state");
  assert.equal(IPC.ITEM_SET_FEEDBACK, "item:set-feedback");
  assert.equal(IPC.MODEL_PRESETS, "model:presets");
  assert.equal(IPC.MODEL_CREATE, "model:create");
  assert.equal(IPC.APPROVAL_RESPOND, "approval:respond");
  assert.equal(IPC.APP_NEW_TASK, "app:new-task");
  assert.equal(IPC.APP_OPEN_WORKSPACE, "app:open-workspace");
  assert.equal(IPC.TERMINAL_CREATE, "terminal:create");
  assert.equal(IPC.TERMINAL_WRITE, "terminal:write");
  assert.equal(IPC.TERMINAL_RESIZE, "terminal:resize");
  assert.equal(IPC.TERMINAL_CLOSE, "terminal:close");
  assert.equal(IPC.TERMINAL_DATA_EVENT, "terminal:data");
  assert.equal(IPC.TERMINAL_EXIT_EVENT, "terminal:exit");
  assert.equal("MODEL_PROFILE_TEST" in IPC, false);
});

void test("Shared constants keep only cross-boundary limits", () => {
  assert.equal(MAX_APPROVAL_FEEDBACK_BYTES, 2_000);
  assert.equal(gitDiffArguments[3], "origin/main");
});

void test("ModelId and RuntimeStatus types are uniquely exported from domain contracts", () => {
  const flashModel: ModelId = "deepseek-v4-flash";
  const startingStatus: RuntimeStatus = { state: "starting" };
  assert.equal(flashModel, "deepseek-v4-flash");
  assert.equal(startingStatus.state, "starting");
});
