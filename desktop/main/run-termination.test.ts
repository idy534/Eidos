import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
import type { RuntimeNotification } from "../shared/domain-contracts.js";

test("accepts persisted Run termination facts through the Main notification boundary", async () => {
  const root = path.resolve(".");
  const fixturePath = path.join(root, "protocol/fixtures/run-termination-v1.json");
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-termination-protocol-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "");
  await writeFile(path.join(packageRoot, "__main__.py"), [
    "import json,pathlib,sys",
    `run=json.loads(pathlib.Path(${JSON.stringify(fixturePath)}).read_text())['run']`,
    "def send(value): print(json.dumps(value),flush=True)",
    "request=json.loads(sys.stdin.readline())",
    "send({'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':1,'runtimeVersion':'fixture','capabilities':{'runShell':False,'modelConfigured':False}}})",
    "send({'jsonrpc':'2.0','method':'run/completed','params':{'sessionId':run['sessionId'],'run':run}})",
    "request=json.loads(sys.stdin.readline())",
    "send({'jsonrpc':'2.0','id':request['id'],'result':{}})",
  ].join("\n"));
  let accept: (notification: RuntimeNotification) => void = () => undefined;
  const completed = new Promise<RuntimeNotification>((resolve) => { accept = resolve; });
  const client = new RuntimeClient({
    pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot,
    onNotification: (notification) => accept(notification),
  });
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await client.initialize();
    const notification = await Promise.race([completed, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("termination notification timeout")), 5000);
    })]);
    assert.equal(notification.method, "run/completed");
    if (notification.method === "run/completed") assert.deepEqual(notification.params.run, fixture.run);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    clearTimeout(timer);
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});
