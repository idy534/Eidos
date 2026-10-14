import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { isQueuedRunChangeResponse, isQueuedRunDeleteRequest, isQueuedRunUpdateRequest } from "./queued-runs.js";

void test("queued message fixtures match both protocol boundaries", () => {
  const fixture = JSON.parse(readFileSync("protocol/fixtures/queued-runs-v1.json", "utf8"));
  for (const request of fixture.validRequests) assert.equal(("userInput" in request ? isQueuedRunUpdateRequest : isQueuedRunDeleteRequest)(request), true);
  for (const request of fixture.invalidRequests) assert.equal(("userInput" in request ? isQueuedRunUpdateRequest : isQueuedRunDeleteRequest)(request), false);
  for (const result of fixture.validResponses) assert.equal(isQueuedRunChangeResponse(result), true);
  for (const result of fixture.invalidResponses) assert.equal(isQueuedRunChangeResponse(result), false);
});
