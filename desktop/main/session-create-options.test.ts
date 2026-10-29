import assert from "node:assert/strict";
import test from "node:test";
import { validateSessionCreateOptions } from "./session-create-options.js";

test("project drafts keep their project ID across the Main boundary", () => {
  assert.deepEqual(validateSessionCreateOptions({ projectId: "project-multi", executionMode: "worktree", includeLocalChanges: true }), {
    projectId: "project-multi", executionMode: "worktree", includeLocalChanges: true,
  });
  for (const projectId of ["", 1, null, "x".repeat(257)]) {
    assert.throws(() => validateSessionCreateOptions({ projectId }));
  }
  assert.throws(() => validateSessionCreateOptions({ projectID: "typo" }));
  assert.deepEqual(validateSessionCreateOptions(undefined), {});
});
