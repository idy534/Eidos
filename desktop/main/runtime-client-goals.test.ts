import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import { isGoalRequest, isGoalView, isGoalRunOrigin, isGoalChangedNotice } from "../shared/goals.js";
import { isRunPlanningOptions } from "../shared/planning.js";
import type { RuntimeNotification } from "../shared/domain-contracts.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
void test("goal fixtures validate in the generated Desktop contract", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/goals-v1.json"), "utf8"));
  for (const value of fixture.validRequests) assert.equal(isGoalRequest(value), true);
  for (const value of fixture.invalidRequests) assert.equal(isGoalRequest(value), false);
  for (const value of fixture.validResponses) assert.equal(isGoalView(value), true);
  for (const value of fixture.invalidResponses) assert.equal(isGoalView(value), false);
  for (const value of fixture.validRunOptions) assert.equal(isRunPlanningOptions(value), true);
  for (const value of fixture.invalidRunOptions) assert.equal(isRunPlanningOptions(value), false);
  assert.equal(isGoalRunOrigin(fixture.origin), true);
  assert.equal(isGoalChangedNotice(fixture.notice), true);
  execFileSync(process.execPath, ["--disallow-code-generation-from-strings", "--input-type=module", "-e",
    'import { isGoalRequest } from "./dist/shared/goals.js"; if (!isGoalRequest({action:"get",sessionId:"11111111-1111-1111-1111-111111111111"})) process.exit(1);'], { cwd: root });
});

void test("goal Runs cross stdio, finish with evidence and persist across restart", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-goals-"));
  const workspace = path.join(temporary, "workspace"); await mkdir(workspace);
  const notices: RuntimeNotification[] = [];
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temporary, "data"),
    environment: { EIDOS_FAKE_MODEL: "goal", EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") },
    onNotification: (notice: RuntimeNotification) => notices.push(notice) };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    const session = await client.createSession(workspace);
    assert.equal((await client.goal({ action: "get", sessionId: session.id })).goal, undefined);
    await assert.rejects(client.startRun(session.id, "返回完成状态", "deepseek-v4-flash", undefined,
      undefined, "full_access", undefined, undefined, { goalMode: true }));
    await writeFile(path.join(workspace, "goal.txt"), "Complete the whole task.\n");
    const reference = await client.prepareInput({ sessionId: session.id, kind: "file", source: path.join(workspace, "goal.txt") });
    await client.startRun(session.id, "返回完成状态", "deepseek-v4-flash", crypto.randomUUID(), undefined, "manual", undefined,
      [reference.id], { goalMode: true });
    const deadline = Date.now() + 12000;
    let view = await client.goal({ action: "get", sessionId: session.id });
    while (view.goal?.status === "active" && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      view = await client.goal({ action: "get", sessionId: session.id });
    }
    assert.equal(view.goal?.status, "complete");
    assert.equal(view.runCount, 2);
    assert.equal(view.goal?.evidence?.length, 1);
    assert.equal(view.usageIncomplete, true);
    assert.ok(notices.some((notice) => notice.method === "goal/updated"));
    const snapshot = await client.readSession(session.id);
    assert.deepEqual(snapshot.runs.map((run) => run.goalOrigin?.trigger), ["user_input", "continuation"]);
    assert.deepEqual(view.goal?.referenceIds, [reference.id]);
    const referenceRows = JSON.parse(execFileSync(path.join(root, ".venv/bin/python"), ["-c",
      "import json,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(json.dumps([json.loads(r[0]) for r in c.execute('SELECT reference_ids_json FROM goal_runs ORDER BY sequence')]))",
      path.join(temporary, "data/state.sqlite")], { encoding: "utf8" }));
    assert.equal(referenceRows.length, 2);
    assert.ok(referenceRows.every((references: string[]) => references.includes(reference.id)));
    assert.ok(snapshot.runs.every((run) => run.status === "succeeded"));
    assert.deepEqual(snapshot.items.filter((item) => item.kind === "user_message").map((item) => item.content), ["返回完成状态"]);
    assert.ok(notices.some((notice) => notice.method === "run/completed" && notice.params.run.goalOrigin));
    await assert.rejects(client.startRun(session.id, "针对旧目标的补充输入", "deepseek-v4-flash", undefined,
      undefined, "manual", undefined, undefined, { goalRevision: view.goal!.revision }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "GOAL_REVISION_CONFLICT");
    assert.equal((await client.readSession(session.id)).runs.length, 2);
    await assert.rejects(client.goal({ action: "clear", sessionId: session.id, expectedRevision: 999 }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "GOAL_REVISION_CONFLICT");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options); await client.initialize();
    assert.equal((await client.goal({ action: "get", sessionId: session.id })).goal?.status, "complete");
    const cleared = await client.goal({ action: "clear", sessionId: session.id, expectedRevision: view.goal!.revision });
    assert.equal(cleared.goal?.status, "cleared");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally { client.terminate(); await client.waitForExit().catch(() => undefined); await rm(temporary, { recursive: true, force: true }); }
});
