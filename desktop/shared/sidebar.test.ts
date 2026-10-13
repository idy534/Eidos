import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { isSidebarPinsResponse, isSidebarSetPinnedRequest } from "./sidebar.js";

void test("sidebar DTOs validate the same bounded strict fixture as Runtime", () => {
  const fixture = JSON.parse(readFileSync("protocol/fixtures/sidebar-v1.json", "utf8")) as {
    validRequests: unknown[]; invalidRequests: unknown[]; validResponses: unknown[]; invalidResponses: unknown[];
  };
  for (const request of fixture.validRequests) assert.equal(isSidebarSetPinnedRequest(request), true);
  for (const request of fixture.invalidRequests) assert.equal(isSidebarSetPinnedRequest(request), false);
  for (const response of fixture.validResponses) assert.equal(isSidebarPinsResponse(response), true);
  for (const response of fixture.invalidResponses) assert.equal(isSidebarPinsResponse(response), false);
});
