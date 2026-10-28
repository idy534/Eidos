import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import { isPersonalizationReadRequest, isPersonalizationSaveRequest, isPersonalizationResponse } from "../shared/personalization.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("personalization fixtures match generated contracts", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/personalization-v1.json"), "utf8"));
  for (const [name, validate] of [["ReadRequests", isPersonalizationReadRequest], ["Requests", isPersonalizationSaveRequest], ["Responses", isPersonalizationResponse]] as const) {
    for (const value of fixture[`valid${name}`]) assert.equal(validate(value), true, JSON.stringify(value));
    for (const value of fixture[`invalid${name}`]) assert.equal(validate(value), false, JSON.stringify(value));
  }
  assert.equal(isPersonalizationSaveRequest({ expectedRevision: 0, preferences: { customInstructions: "x".repeat(16385) } }), false);
});

test("personalization persists and rejects stale or invalid writes through real stdio", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-personalization-"));
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temporary, "data"),
    environment: { EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    const initial = await client.readPersonalization();
    assert.equal(initial.preferences.responseLanguage, "auto");
    const request = { expectedRevision: 0, preferences: { responseLanguage: "zh-CN" as const, preferredName: "小李", customInstructions: "请使用短句。" } };
    const saved = await client.savePersonalization(request);
    assert.equal(saved.revision, 1);
    await assert.rejects(client.savePersonalization(request), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "PERSONALIZATION_CONFLICT");
    await assert.rejects(client.savePersonalization({ expectedRevision: 1, preferences: { tone: "unsupported" } } as never));
    await assert.rejects(client.savePersonalization({ expectedRevision: 1, preferences: { customInstructions: "sk-" + "A".repeat(20) } }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "SENSITIVE_CONTENT_REJECTED");
    assert.deepEqual(await client.readPersonalization(), saved);
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options); await client.initialize();
    assert.deepEqual(await client.readPersonalization(), saved);
    const reset = await client.savePersonalization({ expectedRevision: 1, preferences: {} });
    assert.equal(reset.preferences.customInstructions, "");
    assert.equal(reset.preferences.responseLanguage, "auto");
  } finally {
    await client.shutdown(); await client.waitForExit().catch(() => undefined);
    client.terminate(); await rm(temporary, { recursive: true, force: true });
  }
});
