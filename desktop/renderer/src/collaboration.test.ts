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

test("the collaboration boundary accepts custom types and rejects invalid names", () => {
  const state = structuredClone(fixture.result);
  state.agents[0].role = "reviewer";
  assert.equal(isCollaborationState(state), true);
  state.agents[0].role = "../reviewer";
  assert.equal(isCollaborationState(state), false);
});

test("Python and Desktop agree on long messages and a 32-message batch", () => {
  assert.equal(fixture.result.messages.length, 32);
  assert.ok(fixture.result.messages[0].content.length > 2000);
  assert.equal(isCollaborationState(fixture.result), true);
  const oversized = structuredClone(fixture.result);
  oversized.messages[0].content = "中".repeat(21846);
  assert.equal(isCollaborationState(oversized), false);
  const tooMany = structuredClone(fixture.result);
  tooMany.messages.push({ ...tooMany.messages[0], id: "extra-message" });
  assert.equal(isCollaborationState(tooMany), false);
});
