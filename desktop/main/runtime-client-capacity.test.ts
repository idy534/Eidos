import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { RuntimeClient, type RuntimeNotification } from "./runtime-client.js";
import type { Item, Run, ToolTextPage } from "../shared/domain-contracts.js";

test("shares assistant paging and Run resume contracts across the stdio boundary", async () => {
  const fixturePath = path.resolve("protocol/fixtures/capacity-continuation-v1.json");
  const fixture = JSON.parse(await readFile(fixturePath, "utf8")) as {
    item: Item; pageRequest: { sessionId: string; toolCallId: string; field: "content"; sha256: string; offset: number };
    page: ToolTextPage; resumeRequest: { runId: string; operationId: string }; resumeResponse: { run: Run };
  };
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-capacity-contract-"));
  const pkg = path.join(temp, "eidos_runtime");
  await mkdir(pkg);
  await writeFile(path.join(pkg, "__init__.py"), "");
  await writeFile(path.join(pkg, "__main__.py"), [
    "import json, pathlib, sys",
    `v=json.loads(pathlib.Path(${JSON.stringify(fixturePath)}).read_text())`,
    `base=json.loads(pathlib.Path(${JSON.stringify(path.resolve("protocol/fixtures/v1.json"))}).read_text())`,
    "def receive(): return json.loads(sys.stdin.readline())",
    "def send(value): print(json.dumps(value), flush=True)",
    "req=receive(); assert req['method']=='initialize'",
    "send({**base['initialize']['response'], 'id':req['id']})",
    "send({'jsonrpc':'2.0','method':'item/completed','params':{'sessionId':v['item']['sessionId'],'runId':v['item']['runId'],'item':v['item']}})",
    "for method, params, result in [('toolCall/readText',v['pageRequest'],v['page']),('run/resume',v['resumeRequest'],v['resumeResponse'])]:",
    "    req=receive(); assert req['method']==method and req['params']==params",
    "    send({'jsonrpc':'2.0','id':req['id'],'result':result})",
    "req=receive(); assert req['method']=='runtime/shutdown'",
    "send({'jsonrpc':'2.0','id':req['id'],'result':{}})",
  ].join("\n"));
  const notifications: RuntimeNotification[] = [];
  const client = new RuntimeClient({ pythonExecutable: path.resolve(".venv/bin/python"), runtimeRoot: temp,
    onNotification: (notification) => { notifications.push(notification); } });
  try {
    await client.initialize();
    const request = fixture.pageRequest;
    assert.deepEqual(await client.readToolText(request.sessionId, request.toolCallId, request.field, request.sha256), fixture.page);
    assert.deepEqual(await client.resumeRun(fixture.resumeRequest.runId, fixture.resumeRequest.operationId), fixture.resumeResponse.run);
    const completed = notifications.find((n) => n.method === "item/completed");
    assert.ok(completed && completed.method === "item/completed");
    assert.deepEqual(completed.params.item, fixture.item);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
