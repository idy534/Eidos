import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { isRunPlanningOptions } from "../shared/planning.js";
import { isTeamChangedNotice, isTeamMethod, isTeamRequest, isTeamResponse, isTeamRunOrigin, type TeamMethods } from "../shared/teams.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

interface Cases { valid: unknown[]; invalid: unknown[] }

void test("team fixtures validate in the generated Desktop contract", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/teams-v1.json"), "utf8")) as {
    requests: Record<string, Cases>; responses: Record<string, Cases>; origin: unknown; notice: unknown;
    validRunOptions: unknown[]; invalidRunOptions: unknown[];
  };
  for (const [method, cases] of Object.entries(fixture.requests)) {
    assert.equal(isTeamMethod(method), true, method);
    const key = method as keyof TeamMethods;
    for (const value of cases.valid) assert.equal(isTeamRequest(key, value), true, `${method} ${JSON.stringify(value)}`);
    for (const value of cases.invalid) assert.equal(isTeamRequest(key, value), false, `${method} ${JSON.stringify(value)}`);
  }
  for (const [method, cases] of Object.entries(fixture.responses)) {
    const key = method as keyof TeamMethods;
    for (const value of cases.valid) assert.equal(isTeamResponse(key, value), true, `${method} ${JSON.stringify(value)}`);
    for (const value of cases.invalid) assert.equal(isTeamResponse(key, value), false, `${method} ${JSON.stringify(value)}`);
  }
  assert.equal(isTeamMethod("team/integrate"), false);
  assert.equal(isTeamRunOrigin(fixture.origin), true);
  assert.equal(isTeamChangedNotice(fixture.notice), true);
  for (const value of fixture.validRunOptions) assert.equal(isRunPlanningOptions(value), true, JSON.stringify(value));
  for (const value of fixture.invalidRunOptions) assert.equal(isRunPlanningOptions(value), false, JSON.stringify(value));
});

void test("team mode crosses stdio from proposal to dissolution", { timeout: 40000 }, async () => {
  const { mkdtemp, mkdir, rm } = await import("node:fs/promises");
  const os = await import("node:os");
  const { randomUUID } = await import("node:crypto");
  const { RuntimeClient, RuntimeRequestError } = await import("./runtime-client.js");
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-teams-"));
  const workspace = path.join(temporary, "workspace"); await mkdir(workspace);
  const notices: string[] = [];
  const client = new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: path.join(temporary, "data"),
    environment: { EIDOS_FAKE_MODEL: "team", EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") },
    onNotification: (notice) => notices.push(notice.method),
    onStderr: (line) => { if (/Traceback|ERROR|failed/u.test(line)) process.stderr.write(`[runtime] ${line}\n`); } });
  const until = async <T>(read: () => Promise<T>, done: (value: T) => boolean): Promise<T> => {
    const deadline = Date.now() + 20000;
    let value = await read();
    while (!done(value) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      value = await read();
    }
    assert.ok(done(value), JSON.stringify(value));
    return value;
  };
  try {
    await client.initialize();
    const session = await client.createSession(workspace);
    assert.deepEqual(await client.team("team/read", { sessionId: session.id }), { usage: [] });
    await assert.rejects(client.team("team/messages", { teamId: "missing" }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "TEAM_NOT_ACTIVE");
    const lead = await client.startRun(session.id, "组建团队整理说明", "deepseek-v4-flash", randomUUID(), undefined, "manual",
      undefined, undefined, { teamMode: true });
    const pending = await until(() => client.team("team/read", { sessionId: session.id }), (value) => Boolean(value.proposal));
    assert.equal(pending.proposal?.proposal.members?.[0]?.name, "alice");
    await client.team("team/confirmRoster", { operationId: randomUUID(), proposalId: pending.proposal!.id,
      decision: { confirmed: true, autoWakeLead: false } });
    const active = await until(() => client.team("team/read", { sessionId: session.id }),
      (value) => value.team?.members?.[0]?.runStatus === "succeeded");
    const member = active.team!.members![0]!;
    assert.equal(member.status, "active");
    assert.equal((await client.readSession(session.id)).runs.find((run) => run.id === lead.id)?.status, "succeeded");
    const memberRuns = (await client.readSession(member.sessionId)).runs;
    assert.deepEqual(memberRuns.map((run) => run.teamOrigin?.origin), ["wake"]);
    const page = await client.team("team/messages", { teamId: active.team!.id });
    assert.ok((page.messages ?? []).some((message) => message.authorKind === "system"));
    assert.ok(notices.includes("team/updated"));
    await client.team("team/setStatus", { operationId: randomUUID(), teamId: active.team!.id, status: "dissolving" });
    await until(() => client.team("team/read", { sessionId: session.id }), (value) => !value.team);
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally { client.terminate(); await client.waitForExit().catch(() => undefined); await rm(temporary, { recursive: true, force: true }); }
});

void test("team requests during shutdown reject with RuntimeShutdownError without writing to stdin, while crashes still reject as exited", async () => {
  const { mkdtemp, mkdir, rm, writeFile } = await import("node:fs/promises");
  const os = await import("node:os");
  const { RuntimeClient, RuntimeShutdownError } = await import("./runtime-client.js");

  const startControlled = async (crash: boolean) => {
    const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-team-shutdown-"));
    const runtimeRoot = path.join(temporary, "runtime");
    await mkdir(path.join(runtimeRoot, "eidos_runtime"), { recursive: true });
    await writeFile(path.join(runtimeRoot, "eidos_runtime/__init__.py"), "");
    const requestsPath = path.join(temporary, "requests.jsonl");
    await writeFile(path.join(runtimeRoot, "eidos_runtime/__main__.py"), `
import json, os, sys, time
for line in sys.stdin:
    request = json.loads(line)
    with open(os.environ["TEST_TEAM_REQUESTS"], "a") as output:
        output.write(json.dumps(request) + "\\n")
    if request["method"] == "team/read":
        if os.environ["TEST_TEAM_CRASH"] == "1":
            os._exit(3)
        continue
    if request["method"] == "runtime/shutdown":
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}), flush=True)
        time.sleep(0.08)
        break
`);
    const client = new RuntimeClient({
      pythonExecutable: path.join(root, ".venv/bin/python"),
      runtimeRoot,
      environment: { TEST_TEAM_REQUESTS: requestsPath, TEST_TEAM_CRASH: crash ? "1" : "0" },
    });
    return {
      client,
      requestsPath,
      cleanup: async () => {
        client.terminate();
        await client.waitForExit().catch(() => undefined);
        await rm(temporary, { recursive: true, force: true });
      },
    };
  };

  const graceful = await startControlled(false);
  try {
    const inFlight = graceful.client.team("team/read", { sessionId: "session-1" });
    const inFlightRejection = assert.rejects(
      inFlight,
      (error: unknown) => error instanceof RuntimeShutdownError,
    );
    const shutdown = graceful.client.shutdown();
    await assert.rejects(
      graceful.client.team("team/read", { sessionId: "session-1" }),
      (error: unknown) => error instanceof RuntimeShutdownError,
    );
    await shutdown;
    await inFlightRejection;
    assert.equal(await graceful.client.waitForExit(), 0);
    const recorded = (await readFile(graceful.requestsPath, "utf8"))
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as { method: string });
    assert.deepEqual(recorded.map((entry) => entry.method), ["team/read", "runtime/shutdown"]);
  } finally {
    await graceful.cleanup();
  }

  const crashed = await startControlled(true);
  try {
    await assert.rejects(
      crashed.client.team("team/read", { sessionId: "session-1" }),
      (error: unknown) => error instanceof Error
        && !(error instanceof RuntimeShutdownError)
        && error.message === "Runtime process exited",
    );
    assert.equal(await crashed.client.waitForExit(), 3);
  } finally {
    await crashed.cleanup();
  }
});
