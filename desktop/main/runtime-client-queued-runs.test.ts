import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import type { RuntimeNotification } from "../shared/domain-contracts.js";

void test("queued messages cross Main and Runtime, retain edits after restart, and reject active mutations", { timeout: 30000 }, async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-queued-runs-"));
  const workspace = path.join(temp, "workspace");
  await mkdir(workspace);
  let approvalStarted: (() => void) | undefined;
  const approval = new Promise<void>((resolve) => { approvalStarted = resolve; });
  const notifications: RuntimeNotification[] = [];
  const options = {
    pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: path.join(temp, "data"),
    environment: { EIDOS_FAKE_MODEL: "network-shell", EIDOS_PROJECTLESS_ROOT: path.join(temp, "workspaces"), EIDOS_MCP_OUTPUT_ROOT: path.join(temp, "mcp") },
    onNotification: (notice: RuntimeNotification) => { notifications.push(notice); },
    onApprovalRequest: async () => { approvalStarted?.(); return new Promise<never>(() => {}); },
  };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    await client.createProject("Queue project", workspace);
    const session = await client.createSession(workspace);
    const active = await client.startRun(session.id, "Wait for network approval", "deepseek-v4-flash");
    let approvalTimer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([approval, new Promise<never>((_resolve, reject) => {
        approvalTimer = setTimeout(() => reject(new Error("Approval did not arrive before the queue test deadline")), 8000);
      })]);
    } finally { clearTimeout(approvalTimer); }
    const first = await client.startRun(session.id, "first queued message", "deepseek-v4-flash");
    const second = await client.startRun(session.id, "second queued message", "deepseek-v4-flash");
    assert.equal(first.status, "queued");
    assert.deepEqual((await client.listQueuedRuns({ sessionId: session.id })).items.map((row) => row.runId), [first.id, second.id]);
    await client.updateQueuedRun({ sessionId: session.id, runId: first.id, expectedUserInput: "first queued message", userInput: "edited queued message" });
    await client.deleteQueuedRun({ sessionId: session.id, runId: second.id, expectedUserInput: "second queued message" });
    const steering = await client.startRun(session.id, "guide the active run", "deepseek-v4-flash");
    const receipt = await client.steerQueuedRun({ sessionId: session.id, runId: steering.id, expectedUserInput: "guide the active run", targetRunId: active.id });
    assert.equal(receipt.change.kind, "steered");
    if (receipt.change.kind !== "steered") throw new Error("missing steering receipt");
    const steeringItemId = receipt.change.itemId;
    const steeredHistory = await client.readSession(session.id);
    assert.equal(steeredHistory.items.find((item) => item.id === steeringItemId)?.runId, active.id);
    assert.ok(!steeredHistory.runs.some((run) => run.id === steering.id));
    assert.deepEqual((await client.listQueuedRuns({ sessionId: session.id })).items.map((row) => row.runId), [first.id]);
    await assert.rejects(client.deleteQueuedRun({ sessionId: session.id, runId: active.id, expectedUserInput: "Wait for network approval" }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "QUEUED_RUN_ALREADY_STARTED");
    assert.ok(notifications.some((notice) => notice.method === "run/queueChanged" && notice.params.change.kind === "deleted"));
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options);
    await client.initialize();
    const queue = await client.listQueuedRuns({ sessionId: session.id });
    const history = await client.readSession(session.id);
    assert.equal(history.items.find((item) => item.runId === first.id && item.kind === "user_message")?.content, "edited queued message");
    assert.ok(!history.runs.some((run) => run.id === second.id));
    assert.ok(!queue.items.some((row) => row.runId === second.id));
    // Graceful shutdown retains the existing Run cancellation semantics.
    if (queue.items.some((row) => row.runId === first.id)) {
      await client.deleteQueuedRun({ sessionId: session.id, runId: first.id, expectedUserInput: "edited queued message" });
    }
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
