import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { isMemoryChangedNotice, isMemoryMethod, isMemoryRequest, isMemoryResponse } from "../shared/memory.js";

test("Python and Main share the document-memory protocol fixture", () => {
  const fixture = JSON.parse(readFileSync(path.resolve("protocol/fixtures/memory-v2.json"), "utf8")) as {
    valid: Array<{ method: string; request: unknown; response: unknown }>;
    invalidRequests: Array<{ method: string; request: unknown }>;
    validNotices: unknown[]; invalidNotices: unknown[];
  };
  for (const value of fixture.valid) {
    assert.ok(isMemoryMethod(value.method));
    assert.ok(isMemoryRequest(value.method, value.request), value.method);
    assert.ok(isMemoryResponse(value.method, value.response), value.method);
  }
  for (const value of fixture.invalidRequests) {
    assert.ok(isMemoryMethod(value.method));
    assert.equal(isMemoryRequest(value.method, value.request), false);
  }
  for (const value of fixture.validNotices) assert.equal(isMemoryChangedNotice(value), true);
  for (const value of fixture.invalidNotices) assert.equal(isMemoryChangedNotice(value), false);
});

test("document memory rejects obsolete methods and invented scope or revision fields", () => {
  assert.equal(isMemoryMethod("memory/list"), true);
  for (const name of ["memory/backfill", "memory/jobsRetry", "memory/remember", "memory/manage", "memory/toString", "memory/temporary"]) assert.equal(isMemoryMethod(name), false);
  assert.equal(isMemoryRequest("memory/list", { scope: "allowed" }), true);
  assert.equal(isMemoryRequest("memory/list", { projectId: "another-project" }), true);
  const note = { path: "feedback_testing.md", summary: "只运行相关测试", content: "只运行相关测试。", operationId: "one", expectedRevision: 0 };
  assert.equal(isMemoryRequest("memory/edit", note), true);
  assert.equal(isMemoryRequest("memory/edit", { ...note, expectedRevision: Number.MAX_SAFE_INTEGER + 1 }), false);
  assert.equal(isMemoryRequest("memory/edit", { ...note, mode: "candidate" }), false);
  assert.equal(isMemoryRequest("memory/policy", { mode: "auto" }), true);
  assert.equal(isMemoryRequest("memory/policy", { mode: "uncontrolled" }), false);
  assert.equal(isMemoryResponse("memory/list", { scopes: [], documents: [] }), true);
  assert.equal(isMemoryResponse("memory/list", { scopes: [], documents: [], temporary: true }), false);
  assert.equal(isMemoryResponse("memory/list", { scopes: [], documents: [{ id: "unvalidated" }] }), false);
});

test("memory change notices carry validated metadata only", () => {
  assert.equal(isMemoryChangedNotice({ scopeId: "scope", generation: 3 }), true);
  assert.equal(isMemoryChangedNotice({ scopeId: "scope", generation: 3, content: "private" }), false);
  assert.equal(isMemoryChangedNotice({ scopeId: "scope", generation: -1 }), false);
});
