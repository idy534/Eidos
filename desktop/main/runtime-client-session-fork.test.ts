import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";

test("conversation fork crosses Main and Runtime, survives deletion and restart, and creates no Run", { timeout: 30000 }, async () => {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const temp = await mkdtemp(path.join(os.tmpdir(), "eidos-session-fork-"));
  const workspace = path.join(temp, "workspace");
  await mkdir(workspace);
  const options = {
    pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: path.join(temp, "data"), environment: { EIDOS_PROJECTLESS_ROOT: path.join(temp, "conversations") },
  };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    const source = await client.createSession(workspace);
    await client.renameSession(source.id, "原会话名称");
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    const itemId = randomUUID();
    execFileSync(options.pythonExecutable, ["-c", `
import sys, uuid
from pathlib import Path
from eidos_runtime.db.storage import SessionStore
store = SessionStore(Path(sys.argv[1]))
store.initialize()
for index, text in enumerate(("selected answer", "later answer")):
    run, _ = store.enqueue_run(sys.argv[2], "question " + text)
    identifier = sys.argv[3] if index == 0 else str(uuid.uuid4())
    with store.database.transaction() as connection:
        connection.execute("INSERT INTO items(id,session_id,run_id,ordinal,kind,status,content,created_at,completed_at) VALUES(?,?,?,2,'assistant_message','completed',?,2,2)", (identifier, sys.argv[2], run['id'], text))
        connection.execute("UPDATE runs SET status='succeeded',completed_at=2 WHERE id=?", (run['id'],))
store.close()
`, options.dataDirectory, source.id, itemId], {
      env: { ...process.env, PYTHONPATH: options.runtimeRoot }, encoding: "utf8",
    });
    client = new RuntimeClient(options);
    await client.initialize();
    const request = { operationId: randomUUID(), sourceSessionId: source.id, sourceItemId: itemId, target: { kind: "same_workspace" } } as const;
    const target = await client.forkSession(request);
    assert.notEqual(target.id, source.id);
    assert.equal(target.title, "原会话名称");
    assert.equal((await client.listSessions()).items.find((session) => session.id === target.id)?.title, "原会话名称");
    assert.equal(target.forkedFrom?.itemCount, 2);
    assert.equal((await client.forkSession(request)).id, target.id);
    await assert.rejects(client.forkSession({ ...request, sourceItemId: randomUUID() }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "OPERATION_ID_REUSED");
    const snapshot = await client.readSession(target.id);
    assert.equal(snapshot.runs.length, 1);
    assert.equal(snapshot.runs[0]?.inherited, true);
    assert.deepEqual(snapshot.runs[0]?.allowedActions ?? [], []);
    assert.deepEqual(snapshot.items.map((item) => item.content), ["question selected answer", "selected answer"]);
    assert.ok(snapshot.items.every((item) => item.inherited && item.sessionId === target.id));
    const page = await client.readSession(target.id, { itemLimit: 1 });
    assert.equal(page.items[0]?.content, "selected answer");
    assert.equal((await client.readSession(target.id, { itemLimit: 1, beforeItemId: page.previousItemId! })).items[0]?.kind, "user_message");
    await client.deleteSession(source.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options);
    await client.initialize();
    assert.deepEqual((await client.readSession(target.id)).items, snapshot.items);
    assert.equal((await client.forkSession(request)).id, target.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temp, { recursive: true, force: true });
  }
});
