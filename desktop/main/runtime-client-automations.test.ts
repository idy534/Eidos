import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import { isAutomationRequest, isAutomationResponse, isAutomationOrigin } from "../shared/automation.js";
import type { RuntimeNotification } from "../shared/domain-contracts.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

void test("automation Python schema fixtures validate at the Desktop boundary", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/automations-v1.json"), "utf8"));
  for (const request of fixture.validRequests) assert.equal(isAutomationRequest(request), true, JSON.stringify(request));
  for (const request of fixture.invalidRequests) assert.equal(isAutomationRequest(request), false, JSON.stringify(request));
  for (const response of fixture.validResponses) assert.equal(isAutomationResponse(response), true);
  for (const response of fixture.invalidResponses) assert.equal(isAutomationResponse(response), false);
  assert.equal(isAutomationOrigin(fixture.automationSession.automationOrigin), true);
});

void test("automation validators load and reject invalid data when dynamic code generation is disabled", () => {
  execFileSync(process.execPath, ["--disallow-code-generation-from-strings", "--input-type=module", "-e", `
    import assert from "node:assert/strict";
    import { readFileSync } from "node:fs";
    import { isAutomationRequest, isAutomationResponse, isAutomationHistoryRequest,
      isAutomationRunNowRequest, isAutomationOrigin } from "./dist/shared/automation.js";
    const fixture = JSON.parse(readFileSync("protocol/fixtures/automations-v1.json", "utf8"));
    for (const value of fixture.validRequests) assert.equal(isAutomationRequest(value), true);
    for (const value of fixture.invalidRequests) assert.equal(isAutomationRequest(value), false);
    for (const value of fixture.validResponses) assert.equal(isAutomationResponse(value), true);
    for (const value of fixture.invalidResponses) assert.equal(isAutomationResponse(value), false);
    const id = "11111111-1111-1111-1111-111111111111";
    assert.equal(isAutomationHistoryRequest({ id }), true);
    assert.equal(isAutomationHistoryRequest({ id, unexpected: true }), false);
    assert.equal(isAutomationRunNowRequest({ id, operationId: id }), true);
    assert.equal(isAutomationRunNowRequest({ id, operationId: 1 }), false);
    const origin = { automationId: id, revision: 1, occurrenceId: id, scheduledFor: "2026-10-16T00:00:00Z" };
    assert.equal(isAutomationOrigin(origin), true);
    assert.equal(isAutomationOrigin({ ...origin, revision: "1" }), false);
    const request = structuredClone(fixture.validRequests[1]);
    request.command.name = "😀".repeat(80);
    assert.equal(isAutomationRequest(request), true, "Unicode limits count characters rather than UTF-16 units");
    request.command.name += "😀";
    assert.equal(isAutomationRequest(request), false);
  `], { cwd: root, stdio: "pipe" });
});

void test("automations persist across Runtime restart with typed reads and revision conflicts", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-automation-contract-"));
  const workspace = path.join(temporary, "workspace"); await mkdir(workspace);
  const notifications: string[] = [];
  const scheduledNotices = new Set<string>();
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temporary, "data"),
    environment: { EIDOS_FAKE_MODEL: "write", EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") },
    onNotification: (notice: RuntimeNotification) => {
      notifications.push(notice.method);
      if ((notice.method === "run/started" || notice.method === "run/updated" || notice.method === "run/completed") && notice.params.run.automationOrigin) {
        scheduledNotices.add(notice.params.run.automationOrigin.automationId);
      }
    } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    const session = await client.createSession(workspace);
    const response = await client.automation({ contextSessionId: session.id, operationId: crypto.randomUUID(), command: {
      mode: "create", kind: "cron", name: "状态检查", prompt: "检查状态。", schedule: { expression: "*/10 * * * *", timezone: "Asia/Shanghai" },
      sessionId: session.id, model: { modelId: "deepseek-v4-flash" },
    } });
    assert.equal(response.automation?.approvalMode, "auto_review");
    assert.equal(response.automation?.target.sessionId, session.id);
    const id = response.automation!.id;
    await client.automationRunNow({ id, operationId: crypto.randomUUID() });
    const until = Date.now() + 10000;
    let history = await client.automationHistory({ id });
    while (history.occurrences?.[0]?.runStatus !== "succeeded" && Date.now() < until) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      history = await client.automationHistory({ id });
    }
    assert.equal(history.occurrences?.[0]?.runStatus, "succeeded");
    assert.ok(scheduledNotices.has(id), "live Run events must preserve scheduled execution origin");
    await assert.rejects(client.automationHistory({ id, cursor: "invalid" }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "INVALID_PARAMS");
    const snapshot = await client.readSession(session.id);
    assert.equal(snapshot.runs.find((run) => run.id === history.occurrences?.[0]?.runId)?.automationOrigin?.automationId, id);
    assert.equal(snapshot.session.automationOrigin, undefined, "reusing a Session must preserve its original source");
    const created = await client.automation({ operationId: crypto.randomUUID(), command: {
      mode: "create", kind: "cron", name: "自动任务来源", prompt: "检查状态。", projectId: null,
      schedule: { expression: "*/10 * * * *", timezone: "Asia/Shanghai" }, model: { modelId: "deepseek-v4-flash" },
    } });
    await client.automationRunNow({ id: created.automation!.id, operationId: crypto.randomUUID() });
    let sourceHistory = await client.automationHistory({ id: created.automation!.id });
    const sourceDeadline = Date.now() + 10000;
    while (!sourceHistory.occurrences?.[0]?.runId && Date.now() < sourceDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      sourceHistory = await client.automationHistory({ id: created.automation!.id });
    }
    const generatedSessionId = sourceHistory.occurrences![0]!.sessionId!;
    const generated = await client.readSession(generatedSessionId);
    assert.equal(generated.session.title, "自动任务来源");
    assert.equal(generated.session.automationOrigin?.automationId, created.automation!.id);
    assert.equal((await client.listSessions()).items.find((item) => item.id === generatedSessionId)?.automationOrigin?.automationId, created.automation!.id);
    await client.automation({ operationId: crypto.randomUUID(), command: { mode: "update", id, expectedRevision: 1, changes: { name: "修改后的任务" } } });
    await assert.rejects(client.automation({ operationId: crypto.randomUUID(), command: { mode: "update", id, expectedRevision: 1, changes: { status: "PAUSED" } } }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "AUTOMATION_REVISION_CONFLICT");
    assert.ok(notifications.includes("automation/changed"));
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options); await client.initialize();
    assert.equal((await client.readSession(generatedSessionId)).session.automationOrigin?.automationId, created.automation!.id);
    const restored = await client.automation({ command: { mode: "view", id } });
    assert.equal(restored.automation?.name, "修改后的任务");
    assert.equal(restored.automation?.approvalMode, "auto_review");
    assert.equal(restored.automation?.revision, 2);
    assert.equal((await client.automationHistory({ id })).occurrences?.length, 1);
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally { client.terminate(); await client.waitForExit().catch(() => undefined); await rm(temporary, { recursive: true, force: true }); }
});

void test("Desktop-confirmed automation keeps full access across restart and launches an ordinary Run", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-automation-full-access-"));
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temporary, "data"),
    environment: { EIDOS_FAKE_MODEL: "write", EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    const command = { mode: "create", kind: "cron", name: "本地整理", prompt: "整理目录。", projectId: null,
      schedule: { expression: "*/10 * * * *", timezone: "Asia/Shanghai" }, model: { modelId: "deepseek-v4-flash" }, approvalMode: "full_access" } as const;
    await assert.rejects(client.automation({ operationId: crypto.randomUUID(), command }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "AUTOMATION_CONFIRMATION_REQUIRED");
    const created = await client.automation({ operationId: crypto.randomUUID(), command, fullAccessConfirmation: "full-access-v1" });
    const id = created.automation!.id;
    assert.equal(created.automation?.fullAccessConfirmation, "full-access-v1");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options); await client.initialize();
    const restored = await client.automation({ command: { mode: "view", id } });
    assert.equal(restored.automation?.approvalMode, "full_access");
    assert.equal(restored.automation?.fullAccessConfirmation, "full-access-v1");
    const list = await client.automation({ command: { mode: "view", scope: "all" } });
    assert.equal(list.items?.find((task) => task.id === id)?.approvalMode, "full_access");
    await client.automationRunNow({ id, operationId: crypto.randomUUID() });
    const until = Date.now() + 10000;
    let history = await client.automationHistory({ id });
    while (history.occurrences?.[0]?.runStatus !== "succeeded" && Date.now() < until) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      history = await client.automationHistory({ id });
    }
    const occurrence = history.occurrences![0]!;
    assert.equal(occurrence.runStatus, "succeeded");
    const snapshot = await client.readSession(occurrence.sessionId!);
    assert.equal(snapshot.runs.find((run) => run.id === occurrence.runId)?.approvalMode, "full_access");
    const updated = await client.automation({ operationId: crypto.randomUUID(), command: {
      mode: "update", id, expectedRevision: 1, changes: { approvalMode: "manual" },
    } });
    assert.equal(updated.automation?.fullAccessConfirmation ?? null, null);
    await assert.rejects(client.automation({ operationId: crypto.randomUUID(), command: {
      mode: "update", id, expectedRevision: 2, changes: { approvalMode: "full_access" },
    } }), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "AUTOMATION_CONFIRMATION_REQUIRED");
    const confirmed = await client.automation({ operationId: crypto.randomUUID(), fullAccessConfirmation: "full-access-v1", command: {
      mode: "update", id, expectedRevision: 2, changes: { approvalMode: "full_access" },
    } });
    assert.equal(confirmed.automation?.approvalMode, "full_access");
    assert.equal(confirmed.automation?.fullAccessConfirmation, "full-access-v1");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally { client.terminate(); await client.waitForExit().catch(() => undefined); await rm(temporary, { recursive: true, force: true }); }
});
