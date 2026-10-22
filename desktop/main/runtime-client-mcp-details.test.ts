import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";

void test("MCP tools cross Main/Runtime with native sandbox, paging, authorization and restart", { timeout: 30000, skip: process.platform !== "darwin" }, async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-mcp-details-"));
  const source = path.join(temp, "source");
  await mkdir(source);
  const fixture = await readFile(path.join(root, "runtime/tests/fixtures/mcp_fixture.py"), "utf8");
  await writeFile(path.join(source, "server.py"), fixture.replace(
    '"serverInfo": {"name": "eidos-fixture", "version": "1.0.0"},',
    '"serverInfo": {"name": "eidos-fixture", "version": "1.0.0", "description": "Native discovery fixture"},',
  ));
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temp, "data") };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    await client.createMcpServer({ serverId: "fixture", executable: options.pythonExecutable, argv: ["server.py"],
      env: {}, envNames: [], cwd: source, permissionProfile: "workspace_read", startupTimeoutSeconds: 5, toolTimeoutSeconds: 10 });
    const request = { pluginId: "manual", serverId: "fixture" };
    assert.equal((await client.readMcpTools(request)).state, "not_authorized");
    await client.setMcpEnabled("manual", "fixture", true);
    const first = await client.readMcpTools(request);
    assert.equal(first.state, "ready");
    assert.equal(first.description, "Native discovery fixture");
    assert.equal(first.tools?.length, 10);
    assert.ok(first.nextCursor);
    const next = await client.readMcpTools({ ...request, cursor: first.nextCursor });
    assert.ok(next.tools?.length && next.tools.length <= 10);
    assert.equal(next.nextCursor, null);
    assert.equal(new Set([...first.tools!, ...next.tools!].map(tool => tool.name)).size, first.total);
    const found = await client.readMcpTools({ ...request, cursor: `${first.catalogId}:0`, query: "structured_invalid" });
    assert.equal(found.tools?.[0]?.name, "structured_invalid");
    await client.shutdown(); await client.waitForExit();
    client = new RuntimeClient(options); await client.initialize();
    await assert.rejects(client.readMcpTools({ ...request, cursor: first.nextCursor }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "INVALID_CURSOR");
    await client.setMcpEnabled("manual", "fixture", false);
    assert.equal((await client.readMcpTools(request)).state, "not_authorized");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate(); await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
