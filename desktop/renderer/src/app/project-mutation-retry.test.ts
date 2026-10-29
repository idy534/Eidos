import assert from "node:assert/strict";
import test from "node:test";
import { gitFileTarget } from "./git-workspace-client.js";
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
  const roots = [{ id: "src", path: "/work/repo/src" }, { id: "tests", path: "/work/repo/tests" }];
  assert.deepEqual(gitFileTarget("src/main.ts", "/work/repo", roots), { path: "main.ts", rootId: "src" });
  assert.deepEqual(gitFileTarget("tests/main.ts", "/work/repo", roots), { path: "main.ts", rootId: "tests" });
  assert.deepEqual(gitFileTarget("src/main.ts", "/managed/task", [{ id: "src", path: "/managed/task/src" }]), { path: "main.ts", rootId: "src" });
  for (const path of ["README.md", "src/../secret", "src-other/file", "/src/file", "src/\0file"]) {
    assert.equal(gitFileTarget(path, "/work/repo", roots), undefined);
  }
  assert.deepEqual(gitFileTarget("main.ts", "/", [{ id: "filesystem", path: "/" }]), { path: "main.ts", rootId: "filesystem" });
  assert.deepEqual(gitFileTarget("src/main.ts", "/work/repo", [...roots, { id: "repo", path: "/work/repo" }]), { path: "main.ts", rootId: "src" });
});
