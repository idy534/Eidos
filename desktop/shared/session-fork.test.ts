import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { isInheritedHistoryEntry, isSessionForkRequest, isSessionForkResponse } from "./session-fork.js";

test("conversation fork DTOs agree with the strict Runtime fixture", () => {
  const fixture = JSON.parse(readFileSync("protocol/fixtures/session-fork-v1.json", "utf8")) as {
    validRequests: unknown[]; invalidRequests: unknown[]; validResponses: unknown[]; invalidResponses: unknown[];
    validHistoryEntries: unknown[]; invalidHistoryEntries: unknown[];
  };
  for (const value of fixture.validRequests) assert.equal(isSessionForkRequest(value), true);
  for (const value of fixture.invalidRequests) assert.equal(isSessionForkRequest(value), false);
  for (const value of fixture.validResponses) assert.equal(isSessionForkResponse(value), true);
  for (const value of fixture.invalidResponses) assert.equal(isSessionForkResponse(value), false);
  for (const value of fixture.validHistoryEntries) assert.equal(isInheritedHistoryEntry(value), true);
  for (const value of fixture.invalidHistoryEntries) assert.equal(isInheritedHistoryEntry(value), false);
});
