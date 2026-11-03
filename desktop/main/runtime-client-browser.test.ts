import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
import {
  isBrowserCloseResponse, isBrowserGrantRevokeRequest, isBrowserGrantRevokeResponse, isBrowserStatus, isBrowserStatusRequest,
} from "../shared/browser.js";
import type { ApprovalRequest, RuntimeNotification } from "../shared/index.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

void test("shared browser fixture satisfies the generated Main validators", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/browser-v1.json"), "utf8")) as Record<string, unknown>;
  assert.ok(isBrowserStatusRequest(fixture.statusRequest));
  assert.ok(isBrowserStatus(fixture.status));
  assert.ok(isBrowserGrantRevokeRequest(fixture.revokeRequest));
  assert.ok(isBrowserGrantRevokeResponse(fixture.revokeResponse));
  assert.ok(isBrowserCloseResponse(fixture.closeResponse));
  assert.equal(isBrowserStatus({ ...(fixture.status as object), endpoint: "http://127.0.0.1:9222" }), false);
});

void test("browser access approvals and grant notifications cross Main with closed fields", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-browser-approval-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  const provenance = "{'kind':'mcp','sourceId':'eidos-browser:browser','sourceVersion':'1.0.0','contentHash':'h1','pluginId':'eidos-browser','serverId':'browser'}";
  await writeFile(path.join(packageRoot, "__main__.py"), [
    "import json, sys",
    "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
    "json.loads(sys.stdin.readline())",
    "send({'jsonrpc':'2.0','id':'client-1','result':{'protocolVersion':1,'runtimeVersion':'0.3.0','capabilities':{'runShell':True,'modelConfigured':False}}})",
    `send({'jsonrpc':'2.0','id':'server-browser-1','method':'item/requestApproval','params':{'sessionId':'s1','runId':'r1','itemId':'i1','toolCallId':'tc1','summary':'允许本会话使用 Eidos 浏览器','kind':'browser_access','toolName':'mcp__browser__browser_navigate','arguments':{'url':'https://example.com'},'grantScope':'session','profile':'user','access':'interact','requiresHumanConfirmation':True,'riskWarning':'shared browser','provenance':${provenance},'permissionProfile':'browser','timeoutSeconds':120,'envNames':[],'cookies':'omit'}})`,
    "sys.stdin.readline()",
    `send({'jsonrpc':'2.0','id':'server-browser-2','method':'item/requestApproval','params':{'sessionId':'s1','runId':'r1','itemId':'i2','toolCallId':'tc2','summary':'forged','kind':'browser_access','toolName':'mcp__browser__browser_navigate','arguments':{},'grantScope':'forever','profile':'default','access':'interact','provenance':${provenance},'permissionProfile':'browser','timeoutSeconds':120}})`,
    "reply = json.loads(sys.stdin.readline())",
    // Report the forged request's rejection back through a valid notification.
    "send({'jsonrpc':'2.0','method':'browser/grantChanged','params':{'sessionId':'forged-rejected' if reply.get('error',{}).get('code') == -32601 else 'forged-accepted','active':False}})",
    "sys.stdin.readline()",
  ].join("\n"), "utf8");
  const approvals: ApprovalRequest[] = [];
  const notifications: RuntimeNotification[] = [];
  const client = new RuntimeClient({
    pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot,
    onApprovalRequest: async (request) => { approvals.push(request); return { decision: "approve" }; },
    onNotification: (notification) => { notifications.push(notification); },
  });
  try {
    await client.initialize();
    await new Promise((resolve) => setTimeout(resolve, 800));
    assert.equal(approvals.length, 1);
    const approval = approvals[0]!;
    assert.equal(approval.kind, "browser_access");
    assert.equal(approval.kind === "browser_access" && approval.grantScope, "session");
    assert.equal((approval as unknown as Record<string, unknown>).cookies, undefined);
    assert.equal(approval.riskWarning, "shared browser");
    assert.equal(approval.kind === "browser_access" && approval.profile, "user");
    assert.equal(approval.requiresHumanConfirmation, true);
    assert.deepEqual(notifications.map(({ method, params }) => ({ method, params })), [
      { method: "browser/grantChanged", params: { sessionId: "forged-rejected", active: false } },
    ]);
  } finally {
    await client.shutdown().catch(() => undefined);
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

void test("browser status, revoke and close round-trip through the real Runtime", { timeout: 30000 }, async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-browser-rpc-"));
  const client = new RuntimeClient({
    pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: path.join(temp, "data"),
  });
  try {
    await client.initialize();
    const status = await client.readBrowserStatus({});
    assert.equal(status.pluginInstalled, false);
    assert.equal(status.running, false);
    assert.equal(status.granted, false);
    assert.deepEqual(await client.revokeBrowserGrant({ sessionId: "missing-session" }), { revoked: false });
    assert.deepEqual(await client.closeSharedBrowser(), { closed: false });
    // Nothing was ever opened, so clearing reports no profile and no grants.
    assert.deepEqual(await client.clearSharedBrowserProfile(), { cleared: false, revokedGrants: 0 });
  } finally {
    await client.shutdown().catch(() => undefined);
    await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
