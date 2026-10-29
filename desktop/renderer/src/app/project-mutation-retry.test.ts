import assert from "node:assert/strict";
import test from "node:test";
import { gitFilePath } from "./git-workspace-client.js";
import { ProjectMutationRetry } from "./project-mutation-retry.js";

test("project edit retry reuses an operation ID until the command changes or resets", () => {
  const retry = new ProjectMutationRetry();
  const command = { projectId: "p", expectedRevision: 2, name: "name", roots: [{ id: "r", path: "/work" }], primaryRootId: "r" };
  const first = retry.prepare(command);
  assert.deepEqual(retry.prepare(structuredClone(command)), first);
  assert.notEqual(retry.prepare({ ...command, name: "updated" }).operationId, first.operationId);
  retry.reset();
  assert.notEqual(retry.prepare(command).operationId, first.operationId);
});

test("Git paths stay relative to the mounted subdirectory", () => {
  assert.equal(gitFilePath("src/main.ts", "/work/repo", "/work/repo/src"), "main.ts");
  assert.equal(gitFilePath("src/main.ts", "/managed/task", "/managed/task/src"), "main.ts");
  assert.throws(() => gitFilePath("README.md", "/work/repo", "/work/repo/src"), /不在项目/);
  assert.throws(() => gitFilePath("src/../secret", "/work/repo", "/work/repo/src"));
});
