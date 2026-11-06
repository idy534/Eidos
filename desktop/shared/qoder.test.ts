import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { isQoderConnectionRequest, isQoderConnectionState, isQoderModelSettings } from "./qoder.js";
const fixture = JSON.parse(readFileSync(path.resolve("protocol/fixtures/qoder-model-v1.json"), "utf8"));
test("Qoder shares its closed Python/TypeScript contract", () => {
  assert.ok(isQoderConnectionRequest(fixture.connectionRequest));
  assert.ok(isQoderConnectionRequest(fixture.checkRequest));
  assert.ok(isQoderConnectionState(fixture.checkingState));
  assert.ok(isQoderConnectionState(fixture.connectionState));
  assert.ok(isQoderModelSettings(fixture.create.qoder));
  assert.ok(isQoderModelSettings(fixture.textOnlyCreate.qoder));
  assert.equal(isQoderModelSettings({ supportsToolCall: "false" }), false);
  assert.equal(isQoderConnectionRequest({ action: "sample" }), false);
  assert.equal(isQoderConnectionRequest({ action: "login", token: "unexpected" }), false);
  assert.equal(isQoderConnectionState({ ...fixture.connectionState, apiKey: "unexpected" }), false);
});
