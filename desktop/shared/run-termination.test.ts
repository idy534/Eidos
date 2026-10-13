import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { isRunTermination } from "./run-termination.js";
import { isCollaborationState } from "./collaboration.js";

test("uses the same bounded termination vectors as Python", () => {
  const fixture = JSON.parse(readFileSync(path.resolve("protocol/fixtures/run-termination-v1.json"), "utf8"));
  assert.equal(isRunTermination(fixture.run.termination), true);
  for (const invalid of fixture.invalid) assert.equal(isRunTermination(invalid), false);
  const agent = { id: "agent-1", taskName: "inspect", role: "worker", parentRunId: "parent", sessionId: "child",
    runId: "child-run", status: "failed", task: "inspect", errorCode: "MODEL_PROTOCOL_ERROR", createdAt: 1,
    termination: fixture.run.termination, reconciliationRequired: true,
  };
  assert.equal(isCollaborationState({ agents: [agent], messages: [] }), true);
  assert.equal(isCollaborationState({ agents: [{ ...agent, termination: fixture.invalid[0] }], messages: [] }), false);
});
