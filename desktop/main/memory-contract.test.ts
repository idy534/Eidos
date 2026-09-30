import assert from "node:assert/strict";
import test from "node:test";
import { isMemoryMethod, isMemoryRequest, isMemoryResponse } from "../shared/memory.js";

test("memory IPC schema rejects invented methods, extra fields and unsafe revisions", () => {
  assert.equal(isMemoryMethod("memory/list"), true);
  assert.equal(isMemoryMethod("memory/toString"), false);
  assert.equal(isMemoryMethod("run/start"), false);
  assert.equal(isMemoryRequest("memory/list", {scope: "allowed"}), true);
  assert.equal(isMemoryRequest("memory/list", {projectId: "another-project"}), false);
  assert.equal(isMemoryRequest("memory/manage", {action: "forget", entryId: "id", expectedRevision: "3", operationId: "operation"}), false);
  assert.equal(isMemoryRequest("memory/remember", {content: "", operationId: "operation"}), false);
  assert.equal(isMemoryResponse("memory/list", {scopes: [], entries: [], jobs: [], temporary: false, trigramAvailable: true}), true);
  assert.equal(isMemoryResponse("memory/list", {scopes: [], entries: [{id: "unvalidated"}], jobs: [], temporary: false, trigramAvailable: true}), false);
});
