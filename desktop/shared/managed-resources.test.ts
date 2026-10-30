import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { isResourceStatus, isResourceRetryRequest, isResourceRetryResponse } from "./managed-resources.js";

test("managed resource contracts share Python fixtures and reject extra download parameters", async () => {
  const fixturePath = path.resolve("protocol/fixtures/managed-resources-v1.json");
  const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
  assert.ok(isResourceStatus(fixture.status));
  assert.ok(isResourceRetryRequest(fixture.retryRequest));
  assert.ok(isResourceRetryResponse(fixture.retryResponse));
  assert.equal(isResourceRetryRequest({ ...fixture.retryRequest, url: "https://example.com/asset" }), false);
  assert.equal(isResourceStatus({ ...fixture.status, packs: [{ ...fixture.status.packs[0], phase: "unknown" }] }), false);
});
