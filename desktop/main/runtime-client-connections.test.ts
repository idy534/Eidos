import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
import { isBuiltinPluginsResponse, isMcpConnectionState, isMcpConnectionRequest } from "../shared/mcp-connections.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

void test("remote MCP fixtures share closed Python and TypeScript contracts", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/mcp-connections-v1.json"), "utf8"));
  assert.equal(isMcpConnectionState(fixture.connected), true);
  assert.equal(isMcpConnectionState({ ...fixture.connected, accessToken: "forbidden" }), false);
  assert.equal(isMcpConnectionRequest(fixture.connect), true);
  assert.equal(isBuiltinPluginsResponse(fixture.builtins), true);
});

void test("built-in install and remote credentials cross Main/Runtime and survive restart", { timeout: 30000 }, async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-remote-mcp-"));
  const source = path.join(temp, "source");
  await mkdir(source);
  const http = createServer(async (request, response) => {
    if (request.method !== "POST") { response.writeHead(request.method === "DELETE" ? 204 : 405); response.end(); return; }
    let body = "";
    for await (const part of request) body += part;
    if (request.headers.authorization !== "Bearer fixture-token") { response.writeHead(401); response.end(); return; }
    const message = JSON.parse(body);
    if (!message.id && message.id !== 0) { response.writeHead(202); response.end(); return; }
    const result = message.method === "initialize"
      ? { protocolVersion: message.params.protocolVersion, capabilities: { tools: {} }, serverInfo: { name: "remote", version: "1" } }
      : { tools: [{ name: "read_issue", inputSchema: { type: "object", properties: {} } }] };
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }));
  });
  await new Promise<void>(resolve => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  assert.ok(address && typeof address === "object");
  await writeFile(path.join(source, "plugin.json"), JSON.stringify({ name: "remote-fixture", version: "1.0.0" }));
  await writeFile(path.join(source, "mcp.json"), JSON.stringify({ mcpServers: { remote: {
    type: "streamable-http", url: `http://127.0.0.1:${address.port}/mcp`, extensions: { "com.eidos": { auth: { type: "bearer" } } },
  } } }));
  const bundled = process.env.EIDOS_CONNECTION_TEST_BUNDLE === "1";
  const options = { pythonExecutable: path.join(root, bundled ? "build/macos-runtime/python/bin/python3" : ".venv/bin/python"), runtimeRoot: path.join(root, bundled ? "build/macos-runtime/app" : "runtime"),
    dataDirectory: path.join(temp, "data"), environment: { EIDOS_PROJECTLESS_ROOT: path.join(temp, "workspaces") } };
  let client = new RuntimeClient(options);
  const request = { pluginId: "remote-fixture", serverId: "remote" };
  try {
    await client.initialize();
    assert.equal((await client.builtinPlugins({ action: "list" })).plugins[0]?.id, "eidos-github");
    assert.equal((await client.builtinPlugins({ action: "install", pluginId: "eidos-github" })).plugins[0]?.installed, true);
    const builtin = (await client.listPlugins()).plugins.find(plugin => plugin.id === "eidos-github");
    assert.equal(builtin?.name, "GitHub");
    await client.importPlugin(source);
    await client.setPluginEnabled("remote-fixture", true);
    const pending = await client.mcpConnection({ ...request, action: "connect", method: "token", token: "fixture-token", operationId: "connection-attempt" });
    assert.ok(["connecting", "connected"].includes(pending.status ?? ""));
    let connected = pending;
    for (let index = 0; index < 100 && connected.status === "connecting"; index++) {
      await new Promise(resolve => setTimeout(resolve, 25));
      connected = await client.mcpConnection(request);
    }
    assert.equal(connected.status, "connected");
    const server = (await client.listMcpServers()).servers.find(value => value.serverId === "remote");
    assert.equal(server?.remote?.authType, "bearer");
    assert.equal((await client.readMcpTools(request)).tools?.[0]?.name, "read_issue");
    assert.equal(JSON.stringify(await client.readExtensions()).includes("fixture-token"), false);
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options); await client.initialize();
    assert.equal((await client.mcpConnection(request)).status, "connected");
    assert.equal((await client.readMcpTools(request)).state, "ready");
    assert.equal((await client.mcpConnection({ ...request, action: "disconnect" })).status, "disconnected");
    assert.equal((await client.readMcpTools(request)).state, "not_authorized");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate(); await client.waitForExit().catch(() => undefined);
    http.closeAllConnections();
    await new Promise<void>(resolve => http.close(() => resolve()));
    await rm(temp, { recursive: true, force: true });
  }
});
