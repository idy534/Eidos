import assert from "node:assert/strict";
import { EventEmitter, once } from "node:events";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import type { ApprovalMode, Run, RuntimeNotification } from "../shared/domain-contracts.js";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import "./response-runtime-client.js";

void test("revision approval modes cross Main and Runtime and survive restart", { timeout: 90000 }, async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/response-actions-v1.json"), "utf8")) as {
    validRequests: Array<{ userInput?: string; approvalMode?: ApprovalMode; fullAccessConfirmation?: "full-access-v1" }>;
    invalidRequests: Array<{ approvalMode?: string; fullAccessConfirmation?: string; unexpected?: boolean }>;
  };
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-revision-modes-"));
  const workspace = path.join(temp, "workspace");
  await mkdir(workspace);
  await writeFile(path.join(workspace, "README.md"), "# Revision fixture\n");
  // The default fake model has one exchange. This test needs a bounded supply
  // of replies across several Runs while retaining the real protocol server.
  const runtimeRoot = path.join(temp, "runtime");
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot, { recursive: true });
  await writeFile(path.join(packageRoot, "__init__.py"),
    `${await readFile(path.join(root, "runtime/eidos_runtime/__init__.py"), "utf8")}\n__path__.append(${JSON.stringify(path.join(root, "runtime/eidos_runtime"))})\n`);
  await writeFile(path.join(packageRoot, "__main__.py"), [
    "from eidos_runtime.model.client import ModelResponse, ScriptedModel",
    "from eidos_runtime.protocol import response_server",
    'response_server._model_from_environment = lambda: ScriptedModel([ModelResponse(text="Fixture answer") for _ in range(40)])',
    "raise SystemExit(response_server.run())",
  ].join("\n"));
  const completed = new Map<string, Run>();
  const events = new EventEmitter();
  let approvalRequests = 0;
  const options = {
    pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot,
    dataDirectory: path.join(temp, "data"),
    environment: { EIDOS_FAKE_MODEL: "1", EIDOS_PROJECTLESS_ROOT: path.join(temp, "workspaces"), EIDOS_MCP_OUTPUT_ROOT: path.join(temp, "mcp") },
    onNotification: (notice: RuntimeNotification) => {
      if (notice.method === "run/completed") {
        completed.set(notice.params.run.id, notice.params.run);
        events.emit(notice.params.run.id, notice.params.run);
      }
    },
    onApprovalRequest: async () => { approvalRequests += 1; return { decision: "reject" as const }; },
  };
  async function waitForCompletion(runId: string): Promise<void> {
    const run = completed.get(runId) ?? (await once(events, runId, { signal: AbortSignal.timeout(10000) }))[0] as Run;
    assert.equal(run.status, "succeeded");
  }
  const expectedRuns: Array<{ sessionId: string; runId: string; mode: ApprovalMode }> = [];
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    await client.createProject("Revision modes", workspace);
    for (const request of fixture.validRequests.filter((value) => value.userInput === undefined)) {
      for (const userInput of [undefined, "edited question"]) {
        const session = await client.createSession(workspace);
        const sourceMode = request.approvalMode === "manual" ? "full_access" : "auto_review";
        const source = await client.startRun(session.id, "Read README.md", "deepseek-v4-flash", undefined, undefined,
          sourceMode, sourceMode === "full_access" ? "full-access-v1" : undefined);
        await waitForCompletion(source.id);
        const revision = await client.reviseRun(source.id, userInput, undefined, undefined,
          request.approvalMode, request.fullAccessConfirmation);
        const expectedMode = request.approvalMode ?? "auto_review";
        assert.equal(revision.run.approvalMode ?? "manual", expectedMode);
        assert.equal(revision.kind, userInput === undefined ? "regenerate" : "edit");
        await waitForCompletion(revision.run.id);
        expectedRuns.push({ sessionId: session.id, runId: revision.run.id, mode: expectedMode });
      }
    }

    const session = await client.createSession(workspace);
    const source = await client.startRun(session.id, "Read README.md", "deepseek-v4-flash");
    await waitForCompletion(source.id);
    for (const request of fixture.invalidRequests.filter((value) => value.unexpected === undefined)) {
      await assert.rejects(client.reviseRun(source.id, undefined, undefined, undefined,
        request.approvalMode as ApprovalMode | undefined,
        request.fullAccessConfirmation as "full-access-v1" | undefined),
      (error: unknown) => error instanceof RuntimeRequestError && error.rpcCode === -32602);
    }
    assert.equal((await client.readSession(session.id)).runs.length, 1);
    assert.equal(approvalRequests, 0);

    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options);
    await client.initialize();
    for (const expected of expectedRuns) {
      const snapshot = await client.readSession(expected.sessionId);
      const run = snapshot.runs.find((entry) => entry.id === expected.runId);
      assert.ok(run);
      assert.equal(run.approvalMode ?? "manual", expected.mode);
    }
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
