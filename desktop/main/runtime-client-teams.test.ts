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
