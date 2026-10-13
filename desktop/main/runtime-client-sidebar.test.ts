import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import type { RuntimeNotification } from "../shared/domain-contracts.js";

void test("sidebar pins cross Main and Runtime, survive restart, and clean up on delete", { timeout: 30000 }, async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-sidebar-"));
  const workspace = path.join(temp, "workspace");
  await mkdir(workspace);
  const notifications: RuntimeNotification[] = [];
  const options = {
    pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: path.join(temp, "data"),
    environment: { EIDOS_PROJECTLESS_ROOT: path.join(temp, "workspaces") },
    onNotification: (notice: RuntimeNotification) => { notifications.push(notice); },
  };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    const project = await client.createProject("Pinned project", workspace);
    const session = await client.createSession(workspace);
    const recent = await client.createSession(null, { workspaceNameHint: "Pinned recent" });
    const targets = [
      { kind: "project", id: project.id }, { kind: "session", id: session.id }, { kind: "session", id: recent.id },
    ] as const;
    for (const target of targets) {
      const request = { target, pinned: true, operationId: randomUUID() };
      const first = await client.setSidebarPinned(request);
      assert.deepEqual(await client.setSidebarPinned(request), first);
    }
    assert.deepEqual((await client.listSidebarPins()).items, targets);
    assert.equal(notifications.filter((notice) => notice.method === "sidebar/pinsChanged").length, 3);
    await assert.rejects(client.setSidebarPinned({
      target: { kind: "session", id: randomUUID() }, pinned: true, operationId: randomUUID(),
    }), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "RESOURCE_NOT_FOUND");
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options);
    await client.initialize();
    assert.deepEqual((await client.listSidebarPins()).items, targets);
    await client.deleteSession(session.id);
    await client.deleteProject(project.id);
    await client.deleteSession(recent.id);
    assert.deepEqual((await client.listSidebarPins()).items, []);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
