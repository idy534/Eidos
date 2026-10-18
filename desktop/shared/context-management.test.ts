import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { isContextDetails, isContextCompactionRecord } from "./context-management.js";

test("Python and TypeScript share the closed context management fixture", () => {
  const fixture = JSON.parse(readFileSync(path.resolve("protocol/fixtures/context-management-v1.json"), "utf8"));
  assert.equal(isContextDetails(fixture.details), true);
  assert.equal(isContextCompactionRecord(fixture.compaction), true);
  assert.equal(isContextDetails({ ...fixture.details, rawHistory: "forbidden" }), false);
  assert.equal(isContextCompactionRecord({ ...fixture.compaction, inputTokens: -1 }), false);
  assert.equal(isContextDetails({ ...fixture.details, measurement: "exact_tokenizer" }), false);
});
