import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { isAbsoluteFsPath, RuntimeClient } from "./runtime-client.js";
import type { ApprovalRequest } from "./runtime-client.js";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const pythonExecutable = process.env.EIDOS_PYTHON
  ?? (process.platform === "win32"
    ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
    : path.join(projectRoot, ".venv", "bin", "python"));

void test("isAbsoluteFsPath accepts POSIX, Windows drive, and UNC paths and rejects invalid values", () => {
  assert.equal(isAbsoluteFsPath("/Users/dev/repo"), true);
  assert.equal(isAbsoluteFsPath("C:\\Users\\dev\\repo"), true);
  assert.equal(isAbsoluteFsPath("D:/work/repo"), true);
  assert.equal(isAbsoluteFsPath("\\\\server\\share\\dir"), true);

  assert.equal(isAbsoluteFsPath(""), false);
  assert.equal(isAbsoluteFsPath(null), false);
  assert.equal(isAbsoluteFsPath("relative/path"), false);
  assert.equal(isAbsoluteFsPath("C:relative"), false);
  assert.equal(isAbsoluteFsPath("/bad\0path"), false);
  assert.equal(isAbsoluteFsPath("C:\\bad\0path"), false);
});

void test("RuntimeClient accepts item/requestApproval with Windows drive and UNC paths", { timeout: 20000 }, async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-win-approval-paths-"));
  const runtimeDir = path.join(temp, "runtime");
  const scriptDir = path.join(runtimeDir, "eidos_runtime");
  const dataDir = path.join(temp, "data");
  await mkdir(scriptDir, { recursive: true });
  await mkdir(dataDir, { recursive: true });
  await writeFile(
    path.join(scriptDir, "__main__.py"),
    [
      "import json, sys",
      "def send(msg):",
      "    print(json.dumps(msg), flush=True)",
      "for line in sys.stdin:",
      "    msg = json.loads(line)",
      "    if msg.get('method') == 'initialize':",
      "        send({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':1,'runtimeVersion':'fixture','capabilities':{'runShell':True,'modelConfigured':False}}})",
      "        send({'jsonrpc':'2.0','id':'server-perm-win','method':'item/requestApproval','params':{'sessionId':'s1','runId':'r1','itemId':'i1','toolCallId':'tc1','summary':'grant win','kind':'permission_request','grantScope':'run','permissions':{'network':{'enabled':False},'fileSystem':[{'path':'C:\\\\Users\\\\dev\\\\repo','access':'write','recursive':True},{'path':'\\\\\\\\server\\\\share\\\\dir','access':'read','recursive':False}]}}})",
      "        resp1 = json.loads(sys.stdin.readline())",
      "        assert 'result' in resp1, resp1",
      "        send({'jsonrpc':'2.0','id':'server-file-win','method':'item/requestApproval','params':{'sessionId':'s1','runId':'r1','itemId':'i2','toolCallId':'tc2','summary':'file win','kind':'file_change','diff':'--- a/x\\n+++ b/x\\n','sandboxPermissions':'with_additional_permissions','additionalPermissions':{'fileSystem':[{'path':'D:/work/repo','access':'write','recursive':True}]}}})",
      "        resp2 = json.loads(sys.stdin.readline())",
      "        assert 'result' in resp2, resp2",
      "    elif msg.get('method') == 'runtime/shutdown':",
      "        send({'jsonrpc':'2.0','id':msg['id'],'result':{}})",
      "        break",
    ].join("\n"),
    "utf8",
  );

  const received: ApprovalRequest[] = [];
  const client = new RuntimeClient({
    pythonExecutable,
    runtimeRoot: runtimeDir,
    dataDirectory: dataDir,
    onApprovalRequest: async (request) => {
      received.push(request);
      return { decision: "approve" };
    },
  });

  try {
    await client.initialize();
    const deadline = Date.now() + 10000;
    while (received.length < 2 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    assert.equal(received.length, 2);

    const perm = received[0]!;
    assert.equal(perm.kind, "permission_request");
    assert.deepEqual(perm.permissions.fileSystem, [
      { path: "C:\\Users\\dev\\repo", access: "write", recursive: true },
      { path: "\\\\server\\share\\dir", access: "read", recursive: false },
    ]);

    const fileReq = received[1]!;
    assert.equal(fileReq.kind, "file_change");
    assert.deepEqual(fileReq.additionalFileSystemAccess, [
      { path: "D:/work/repo", access: "write", recursive: true },
    ]);

    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
