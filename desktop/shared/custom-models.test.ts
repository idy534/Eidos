import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { isCustomModelSettings, isCustomProviderResult } from "./custom-models.js";

test("custom model contracts accept the shared fixture and reject unknown fields", () => {
  const fixture = JSON.parse(readFileSync("protocol/fixtures/custom-models-v1.json", "utf8"));
  assert.equal(isCustomModelSettings(fixture.create.custom), true);
  assert.equal(isCustomModelSettings({ ...fixture.create.custom, providerId: "invalid provider" }), false);
  assert.equal(isCustomModelSettings({ ...fixture.create.custom, providerId: "" }), false);
  assert.equal(isCustomProviderResult(fixture.discovered), true);
  assert.equal(isCustomProviderResult({ ...fixture.discovered, apiKey: "secret" }), false);
  assert.equal(isCustomModelSettings({ ...fixture.create.custom, contextWindowTokens: "128000" }), false);
  assert.equal(isCustomProviderResult({ state: "success", message: "ok", models: [{ id: "model", arbitrary: true }] }), false);
});
