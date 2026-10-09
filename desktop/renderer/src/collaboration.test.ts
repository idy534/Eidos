import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { isCollaborationState } from "../../shared/collaboration.js";

const fixture = JSON.parse(readFileSync(path.join(process.cwd(), "protocol/fixtures/collaboration.json"), "utf8"));

test("the shared Python/Desktop fixture accepts all three built-in agent roles", () => {
  const state: unknown = fixture.result;
  assert.ok(isCollaborationState(state));
  assert.ok(state.agents);
  assert.deepEqual(state.agents.map((agent) => agent.role), ["default", "explorer", "worker"]);
});

test("the collaboration boundary rejects unsupported custom roles", () => {
  const state = structuredClone(fixture.result);
  state.agents[0].role = "reviewer";
  assert.equal(isCollaborationState(state), false);
});
