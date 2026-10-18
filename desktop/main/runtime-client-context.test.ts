import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
import type { RuntimeNotification } from "./runtime-client.js";

test("context details and durable manual compaction cross the stdio boundary and restart", async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-context-api-"));
  const workspace = path.join(temporary, "workspace");
  await mkdir(workspace);
  await writeFile(path.join(workspace, "README.md"), "# Context fixture\n");
  let client: RuntimeClient | undefined;
  const notifications: RuntimeNotification[] = [];
  let completed!: () => void;
  const create = () => new RuntimeClient({
    pythonExecutable: path.resolve(".venv/bin/python"), runtimeRoot: path.resolve("runtime"),
    dataDirectory: path.join(temporary, "data"), environment: { EIDOS_FAKE_MODEL: "1" },
    onNotification: (notification: RuntimeNotification) => {
      notifications.push(notification);
      if (notification.method === "run/completed") completed?.();
    },
  });
  async function run(sessionId: string) {
    const done = new Promise<void>((resolve) => { completed = resolve; });
    const result = await client!.startRun(sessionId, "Read README.md and report", "deepseek-flash");
    await Promise.race([done, new Promise<never>((_, reject) => setTimeout(() => reject(new Error("context run timed out")), 15000).unref())]);
    return result;
  }
  try {
    client = create();
    await client.initialize();
    const session = await client.createSession(workspace, { executionMode: "local" });
    const first = await run(session.id);
    const details = await client.readContextDetails(first.id);
    assert.ok(details.activeTokens && details.activeTokens > 0);
    assert.equal(details.measurement, "tokenizer_estimate");
    assert.ok(details.sections?.some((value) => value.name === "tools"));
    assert.equal(details.sections?.reduce((sum, value) => sum + value.tokens, 0), details.activeTokens);
    const usage = await client.readContextUsage(first.id);
    assert.equal(usage?.activeTokens, details.activeTokens);
    assert.equal(usage?.windowTokens, details.contextWindowTokens);
    const request = await client.requestContextCompaction(first.id);
    assert.equal(request.status, "queued");
    assert.deepEqual(await client.requestContextCompaction(first.id), request);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = create();
    await client.initialize();
    const second = await run(session.id);
    const after = await client.readContextDetails(second.id);
    assert.equal(after.manualPending, false);
    assert.equal(after.compaction?.status, "succeeded");
    assert.equal(after.compaction?.requestId, request.requestId);
    assert.ok(after.compaction?.afterTokens && after.compaction.afterTokens > 0);
    assert.equal(after.compaction?.strategy, "deterministic");
    const activities = notifications.filter((notification) => notification.method === "context/compactionUpdated")
      .map((notification) => notification.params.activity).filter((activity) => activity.requestId === request.requestId);
    assert.equal(activities[0]?.status, "running");
    assert.equal(activities.at(-1)?.status, "succeeded");
    const restored = await client.readSession(session.id);
    const history = restored.contextCompactions?.filter((activity) => activity.requestId === request.requestId);
    assert.equal(history?.length, 1);
    assert.equal(history?.[0]?.status, "succeeded");
    assert.equal(history?.[0]?.startedAt, activities[0]?.startedAt);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client?.terminate();
    await client?.waitForExit().catch(() => undefined);
    await rm(temporary, { recursive: true, force: true });
  }
});
