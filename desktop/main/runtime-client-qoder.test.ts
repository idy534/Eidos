import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("Qoder config and status roundtrip through the real Runtime and restart", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-qoder-rpc-"));
  const createClient = () => new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory });
  let client = createClient();
  try {
    await client.initialize();
    const status = await client.qoderConnection({ action: "status" });
    assert.equal(typeof status.resourceReady, "boolean");
    assert.equal(status.phase, "idle");
    const checked = await client.qoderConnection({ action: "check" });
    assert.equal(checked.phase, "idle");
    assert.equal(checked.errorCode, undefined);
    // Source checkouts may already have the published resource pack installed.
    if (!status.resourceReady) {
      assert.equal((await client.qoderConnection({ action: "login" })).errorCode, "resource_not_ready");
    }
    const qoder = { accountRef: "default" as const, contextWindowTokens: 64000, maxOutputTokens: 4096 };
    const model = await client.createModel({ provider: "qoder", modelId: "efficient", qoder });
    assert.equal(model.provider, "qoder");
    assert.equal(model.url, undefined);
    assert.equal(model.apiKey, "");
    assert.equal(model.supportsImages, false);
    assert.deepEqual(model.qoder, { contextWindowTokens: 64000, maxOutputTokens: 4096 });
    assert.equal((await client.listModels()).models[0]?.modelId, "efficient");
    await client.updateModel({ id: model.id, provider: "qoder", modelId: "fast", qoder: { ...qoder, supportsImages: true, supportsToolCall: false } });
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = createClient();
    await client.initialize();
    assert.equal((await client.listModels()).models[0]?.modelId, "fast");
    assert.equal((await client.listModels()).models[0]?.supportsToolCall, false);
    assert.equal((await client.listModels()).models[0]?.supportsImages, true);
    await assert.rejects(client.createModel({ provider: "qoder", modelId: "efficient", qoder, apiKey: "unexpected" }));
    assert.deepEqual(await client.health(), { state: "ready" });
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate(); await client.waitForExit().catch(() => undefined);
    await rm(dataDirectory, { recursive: true, force: true });
  }
});
