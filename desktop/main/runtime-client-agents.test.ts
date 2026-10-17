import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
import { isAgentCatalog, isCollaborationState } from "../shared/collaboration.js";
import { isAgentDefinitionDocument, isAgentDefinitionSaveRequest } from "../shared/agent-definitions.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

void test("custom Agent fixtures agree with the Python contracts", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/custom-agents.json"), "utf8"));
  assert.equal(isAgentCatalog(fixture.catalog), true);
  assert.equal(isAgentCatalog(fixture.invalidCatalog), false);
  assert.equal(isCollaborationState(fixture.state), true);
  assert.equal(isAgentDefinitionDocument(fixture.management.document), true);
  assert.equal(isAgentDefinitionSaveRequest(fixture.management.save), true);
  assert.equal(isAgentDefinitionSaveRequest(fixture.management.invalidSave), false);
  fixture.state.agents[0].role = "../invalid";
  assert.equal(isCollaborationState(fixture.state), false);
});

void test("custom Agent catalog and input references survive typed Runtime transport and restart", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-custom-agents-"));
  const data = path.join(temporary, "data");
  const agents = path.join(data, "agents");
  const workspace = path.join(temporary, "workspace");
  await mkdir(data, { mode: 0o700 });
  await mkdir(agents, { mode: 0o700 }); await mkdir(workspace);
  await writeFile(path.join(workspace, "README.md"), "Fixture workspace");
  await writeFile(path.join(agents, "luna-work.toml"), 'schema_version = 1\nname = "luna-work"\ndescription = "负责调查"\nbase_role = "explorer"\ninstructions = "你需要报告源码证据。"\n');
  const stderr: string[] = [];
  const options = { onStderr: (line: string) => { stderr.push(line); }, pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: data,
    environment: { EIDOS_FAKE_MODEL: "1", EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    assert.equal((await client.listAgentTypes()).agents?.[0]?.name, "luna-work");
    const managed = await client.saveAgentDefinition({ definition: { schemaVersion: 1, name: "desktop-agent", description: "桌面创建", instructions: "保留原始指令。" } });
    assert.equal((await client.readAgentDefinition(managed.source)).version, managed.version);
    const managedReference = await client.prepareInput({ kind: "agent", source: "desktop-agent" });
    const edited = await client.saveAgentDefinition({ source: managed.source, expectedVersion: managed.version, definition: { ...managed.definition, instructions: "更新后的指令。" } });
    assert.match((await client.readInput(managedReference.id)).text, /保留原始指令/);
    await assert.rejects(client.saveAgentDefinition({ source: managed.source, expectedVersion: managed.version, definition: managed.definition }), /AGENT_DEFINITION_CONFLICT/);
    await assert.rejects(client.saveAgentDefinition({ definition: managed.definition }), /AGENT_DEFINITION_EXISTS/);
    await assert.rejects(client.saveAgentDefinition({ definition: { ...managed.definition, model: { providerId: "volcengine", configId: "missing" } } }), /AGENT_MODEL_NOT_AVAILABLE/);
    await assert.rejects(client.readAgentDefinition(path.join(temporary, "outside.toml")), /AGENT_DEFINITION_INVALID/);
    await client.removeAgentDefinition({ source: edited.source, expectedVersion: edited.version });
    assert.match((await client.readInput(managedReference.id)).text, /保留原始指令/);
    const session = await client.createSession(workspace);
    const reference = await client.prepareInput({ kind: "agent", source: "luna-work" });
    assert.equal(reference.kind, "agent");
    assert.match((await client.readInput(reference.id)).text, /你需要报告源码证据/);
    const run = await client.startRun(session.id, "调查项目", "deepseek-v4-flash", crypto.randomUUID(), undefined, undefined, undefined, [reference.id]);
    assert.equal(run.sessionId, session.id);
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    await rm(path.join(agents, "luna-work.toml"));
    client = new RuntimeClient(options); await client.initialize();
    assert.deepEqual((await client.listAgentTypes()).agents, []);
    assert.match((await client.readInput(reference.id)).text, /你需要报告源码证据/);
    assert.equal((await client.readSession(session.id)).items.some((item) => item.references?.some((value) => value.id === reference.id)), true);
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } catch (error) {
    throw new Error(`Runtime Agent integration failed: ${stderr.slice(-30).join("\n")}`, { cause: error });
  } finally {
    client.terminate(); await client.waitForExit().catch(() => undefined);
    await rm(temporary, { recursive: true, force: true });
  }
});
