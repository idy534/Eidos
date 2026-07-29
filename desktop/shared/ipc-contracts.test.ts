import { test } from "node:test";
import assert from "node:assert/strict";
import type { EidosRuntimeAPI } from "./ipc-api.js";
import { IPC } from "./ipc-channels.js";
import { VALID_MODEL_IDS, MAX_APPROVAL_FEEDBACK_BYTES } from "./constants.js";
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

void test("IPC channel object provides central authoritative channels", () => {
  assert.equal(IPC.RUNTIME_GET_STATUS, "runtime:get-status");
  assert.equal(IPC.RUNTIME_HEALTH, "runtime:health");
  assert.equal(IPC.RUN_START, "run:start");
  assert.equal(IPC.RUN_CANCEL, "run:cancel");
  assert.equal(IPC.APPROVAL_RESPOND, "approval:respond");
  assert.equal(IPC.APP_NEW_TASK, "app:new-task");
  assert.equal(IPC.APP_OPEN_WORKSPACE, "app:open-workspace");
});

void test("Shared constants provide authoritative limits and model validation", () => {
  assert.equal(MAX_APPROVAL_FEEDBACK_BYTES, 2_000);
  assert.equal(VALID_MODEL_IDS.has("deepseek-v4-flash"), true);
  assert.equal(VALID_MODEL_IDS.has("deepseek-v4-pro"), true);
  assert.equal(VALID_MODEL_IDS.size, 2);
});

void test("ModelId and RuntimeStatus types are uniquely exported from domain contracts", () => {
  const flashModel: ModelId = "deepseek-v4-flash";
  const startingStatus: RuntimeStatus = { state: "starting" };
  assert.equal(flashModel, "deepseek-v4-flash");
  assert.equal(startingStatus.state, "starting");
});
