import assert from "node:assert/strict";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";

test("resource RPC crosses stdio and core remains usable while downloads are offline", async () => {
  const root = path.resolve(import.meta.dirname, "../..");
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-resource-client-"));
  const client = new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(dataDirectory, "data"), environment: { HOME: path.join(dataDirectory, "home"), HTTPS_PROXY: "http://127.0.0.1:9", ALL_PROXY: "http://127.0.0.1:9", NO_PROXY: "" } });
  try {
    await client.initialize();
    const status = await client.getResourceStatus();
    const hasIndex = await access(path.join(root, "build/macos-runtime/resource-index.json")).then(() => true, () => false);
    assert.equal(status.available, hasIndex);
    if (hasIndex) assert.ok(status.packs?.some(pack => pack.id === "document-python"));
    else assert.deepEqual(status.packs, []);
    assert.ok(status.directory.endsWith("/Eidos/Resource"));
    assert.deepEqual(await client.retryResource({ packId: "document-python" }), { accepted: status.available });
    assert.deepEqual(await client.health(), { state: "ready" });
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally { client.terminate(); await client.waitForExit().catch(() => undefined); await rm(dataDirectory, { recursive: true, force: true }); }
});


async function controlledResourceRuntime(crash: boolean) {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-resource-shutdown-"));
  const runtimeRoot = path.join(temporary, "runtime");
  await mkdir(path.join(runtimeRoot, "eidos_runtime"), { recursive: true });
  await writeFile(path.join(runtimeRoot, "eidos_runtime/__init__.py"), "");
  const requests = path.join(temporary, "requests.jsonl");
  await writeFile(path.join(runtimeRoot, "eidos_runtime/__main__.py"), `
import json, os, sys, time
for line in sys.stdin:
    request = json.loads(line)
    with open(os.environ["TEST_RESOURCE_REQUESTS"], "a") as output:
        output.write(json.dumps(request) + "\\n")
    if request["method"] == "resource/status":
        if os.environ["TEST_RESOURCE_CRASH"] == "1":
            os._exit(3)
        # Leave this read pending so shutdown closes the actual stdio request.
        continue
    if request["method"] == "runtime/shutdown":
        print(json.dumps({"jsonrpc":"2.0", "id":request["id"], "result":{}}), flush=True)
        time.sleep(0.05)
        break
`);
  const root = path.resolve(import.meta.dirname, "../..");
  const client = new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot,
    environment: { TEST_RESOURCE_REQUESTS: requests, TEST_RESOURCE_CRASH: crash ? "1" : "0" } });
  return { client, requests, cleanup: async () => {
    client.terminate();
    await client.waitForExit();
    await rm(temporary, { recursive: true, force: true });
  } };
}

test("resource polling in flight and after shutdown returns unavailable without a new request", async () => {
  const fixture = await controlledResourceRuntime(false);
  try {
    const pendingStatus = fixture.client.getResourceStatus();
    const shutdown = fixture.client.shutdown();
    const unavailable = { available: false, directory: "", packs: [] };
    assert.deepEqual(await fixture.client.getResourceStatus(), unavailable);
    assert.deepEqual(await fixture.client.retryResource({ packId: "document-python" }), { accepted: false });
    assert.deepEqual(await pendingStatus, unavailable);
    await shutdown;
    assert.equal(await fixture.client.waitForExit(), 0);
    assert.deepEqual(await fixture.client.getResourceStatus(), unavailable);
    const requests = (await readFile(fixture.requests, "utf8")).trim().split("\n").map(line => JSON.parse(line));
    assert.deepEqual(requests.map(request => request.method), ["resource/status", "runtime/shutdown"]);
  } finally { await fixture.cleanup(); }
});

test("resource status still rejects an unexpected Runtime exit", async () => {
  const fixture = await controlledResourceRuntime(true);
  try {
    await assert.rejects(fixture.client.getResourceStatus(), /Runtime process exited/);
    assert.equal(await fixture.client.waitForExit(), 3);
  } finally { await fixture.cleanup(); }
});
