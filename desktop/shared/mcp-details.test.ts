import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { isMcpToolsRequest, isMcpToolsResponse } from "./mcp-details.js";

test("MCP details share the Python fixture and reject expanded pages and unknown fields", () => {
  const fixture = JSON.parse(readFileSync("protocol/fixtures/mcp-details-v1.json", "utf8"));
  assert.ok(isMcpToolsRequest(fixture.request));
  assert.ok(isMcpToolsResponse(fixture.response));
  assert.equal(isMcpToolsRequest({ ...fixture.request, limit: 20 }), false);
  assert.equal(isMcpToolsRequest({ ...fixture.request, cursor: "unbounded-cursor" }), false);
  assert.equal(isMcpToolsResponse({ ...fixture.response, tools: Array(11).fill(fixture.response.tools[0]) }), false);
  assert.equal(isMcpToolsResponse({ ...fixture.response, description: 123 }), false);
});
