import assert from "node:assert/strict";
import test from "node:test";

import type { RuntimeStatus } from "./contracts.js";
import { deriveRuntimePresentation } from "./session-state.js";

test("starting state shows neutral animated dot", () => {
  const pres = deriveRuntimePresentation({ state: "starting" });
  assert.equal(pres.tone, "neutral");
  assert.equal(pres.animated, true);
  assert.ok(pres.label.length > 0);
});

test("error state shows danger tone and message", () => {
  const pres = deriveRuntimePresentation({ state: "error", message: "Python failed to start" });
  assert.equal(pres.tone, "danger");
  assert.equal(pres.description, "Python failed to start");
  assert.ok(pres.label.length > 0);
});

test("ready + storage ready shows success tone", () => {
  const status: RuntimeStatus = {
    state: "ready",
    protocolVersion: 1,
    runtimeVersion: "0.3.0",
    runShell: true,
    modelConfigured: true,
    storageHealth: { state: "ready" },
  };
  const pres = deriveRuntimePresentation(status);
  assert.equal(pres.tone, "success");
  assert.equal(pres.animated, undefined);
});

test("ready + health_only shows warning tone with description", () => {
  const status: RuntimeStatus = {
    state: "ready",
    protocolVersion: 1,
    runtimeVersion: "0.3.0",
    runShell: true,
    modelConfigured: true,
    storageHealth: { state: "health_only", code: "JOURNAL_CORRUPT" },
  };
  const pres = deriveRuntimePresentation(status);
  assert.equal(pres.tone, "warning");
  assert.ok(pres.description?.includes("JOURNAL_CORRUPT"), `Expected description to mention the error code, got: ${pres.description}`);
});
