import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import { isChannelResponse, isChannelSaveRequest, isChannelReadRequest } from "../shared/channels.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

void test("channel fixtures enforce the generated Python boundary", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/channels-v1.json"), "utf8"));
  for (const value of fixture.validReadRequests) assert.equal(isChannelReadRequest(value), true);
  for (const value of fixture.invalidReadRequests) assert.equal(isChannelReadRequest(value), false);
  for (const value of fixture.validRequests) assert.equal(isChannelSaveRequest(value), true);
  for (const value of fixture.invalidRequests) assert.equal(isChannelSaveRequest(value), false);
  for (const value of fixture.validResponses) assert.equal(isChannelResponse(value), true);
  for (const value of fixture.invalidResponses) assert.equal(isChannelResponse(value), false);
  assert.equal(isChannelResponse({ state: "connected", clientSecret: "leak" }), false);
});

void test("channel configuration echoes secret, is revision guarded and survives Runtime restart", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-channel-contract-"));
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: path.join(temporary, "data"), environment: { EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    assert.equal((await client.readChannel()).connection ?? null, null);
    const request = { expectedRevision: 0, clientSecret: "sample-channel-secret", config: {
      enabled: false, clientId: "app", modelId: "deepseek-v4-flash", reasoningSelection: "high" as const,
    } };
    const saved = await client.saveChannel(request);
    assert.equal(saved.connection?.revision, 1);
    assert.equal(saved.connection?.hasSecret, true);
    assert.equal(saved.connection?.clientSecret, "sample-channel-secret");
    assert.equal(Object.hasOwn(saved.connection?.config ?? {}, "robotCode"), false);
    assert.equal(Object.hasOwn(saved.connection?.config ?? {}, "corpId"), false);
    await assert.rejects(client.saveChannel(request), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "INVALID_PARAMS");
    const full = { expectedRevision: 1, config: { ...request.config, approvalMode: "full_access" as const }, clientSecret: "sample-channel-secret" };
    await assert.rejects(client.saveChannel(full), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "INVALID_PARAMS");
    const confirmed = await client.saveChannel({ ...full, fullAccessConfirmation: "full-access-v1" });
    assert.equal(confirmed.connection?.revision, 2);
    assert.equal(confirmed.connection?.config.approvalMode, "full_access");
    for (const channel of ["feishu", "wecom"] as const) {
      const extra = await client.saveChannel({ ...request, channel });
      assert.equal(extra.connection?.channel, channel);
      assert.equal(extra.connection?.revision, 1);
      assert.equal((await client.readChannel({ channel })).connection?.channel, channel);
    }
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options);
    await client.initialize();
    const restored = await client.readChannel();
    assert.equal(restored.connection?.revision, 2);
    assert.equal(restored.connection?.config.clientId, "app");
    assert.equal(restored.connection?.clientSecret, "sample-channel-secret");
    assert.equal(restored.connection?.config.approvalMode, "full_access");
    assert.equal(restored.state, "disabled");
    assert.equal(restored.connection?.config.reasoningSelection, "high");
    for (const channel of ["feishu", "wecom"] as const) {
      assert.equal((await client.readChannel({ channel })).connection?.revision, 1);
    }
    const retained = await client.saveChannel({ ...full, expectedRevision: 2, clientSecret: "sample-channel-secret" });
    assert.equal(retained.connection?.revision, 3);
    await client.saveChannel({ expectedRevision: 3, config: { ...request.config, approvalMode: "manual" }, clientSecret: "sample-channel-secret" });
    await assert.rejects(client.saveChannel({ ...full, expectedRevision: 4 }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "INVALID_PARAMS");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temporary, { recursive: true, force: true });
  }
});
