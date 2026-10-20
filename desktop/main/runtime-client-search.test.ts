import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
import { isSearchRequest, isSearchResponse, isFileFindRequest, isFileFindResponse } from "../shared/search.js";

test("search contracts and real Runtime reads preserve scope, anchors and file versions", async () => {
  assert.equal(isSearchRequest({ query: "中文" }), true);
  assert.equal(isSearchRequest({ query: "x", extra: true }), false);
  assert.equal(isSearchResponse({ items: [], indexing: false }), true);
  const root = fileURLToPath(new URL("../..", import.meta.url));
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/search.json"), "utf8"));
  for (const value of fixture.validRequests) assert.equal(isSearchRequest(value), true);
  for (const value of fixture.invalidRequests) assert.equal(isSearchRequest(value), false);
  for (const value of fixture.validResponses) assert.equal(isSearchResponse(value), true);
  for (const value of fixture.invalidResponses) assert.equal(isSearchResponse(value), false);
  for (const value of fixture.validFileRequests) assert.equal(isFileFindRequest(value), true);
  for (const value of fixture.invalidFileRequests) assert.equal(isFileFindRequest(value), false);
  for (const value of fixture.validFileResponses) assert.equal(isFileFindResponse(value), true);
  for (const value of fixture.invalidFileResponses) assert.equal(isFileFindResponse(value), false);
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-search-"));
  const workspace = path.join(temporary, "workspace"); await mkdir(workspace);
  const content = "中文正文 ".repeat(120000) + "needle at the end\n";
  await writeFile(path.join(workspace, "sample.txt"), content);
  const client = new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: path.join(temporary, "data"), environment: { ...process.env, EIDOS_PROJECTLESS_ROOT: path.join(temporary, "conversations") } });
  try {
    await client.initialize();
    const session = await client.createSession(workspace);
    await client.renameSession(session.id, "搜索中文标题");
    const result = await client.searchSessions({ query: "中文" });
    assert.equal(result.items[0]?.sessionId, session.id);
    const preview = await client.readWorkspaceFilePreview(session.id, "sample.txt");
    assert.ok(preview.version);
    assert.equal(preview.truncated, true);
    const full = await client.readWorkspaceFileForFind({ sessionId: session.id, path: "sample.txt", version: preview.version });
    assert.equal(full.content, content);
    assert.equal(full.truncated, false);
    await writeFile(path.join(workspace, "large.txt"), "中".repeat(800000));
    const largePreview = await client.readWorkspaceFilePreview(session.id, "large.txt");
    const partial = await client.readWorkspaceFileForFind({ sessionId: session.id, path: "large.txt", version: largePreview.version! });
    assert.equal(partial.truncated, true);
    assert.equal(partial.content, "中".repeat(Math.floor(2 * 1024 * 1024 / 3)));
    await writeFile(path.join(workspace, "sample.txt"), "changed");
    await assert.rejects(client.readWorkspaceFileForFind({ sessionId: session.id, path: "sample.txt", version: preview.version }), /WORKSPACE_FILE_CHANGED/);
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally { client.terminate(); await client.waitForExit().catch(() => undefined); await rm(temporary, { recursive: true, force: true }); }
});
