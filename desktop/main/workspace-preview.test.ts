import assert from "node:assert/strict";
import test from "node:test";
import { RuntimeRequestError } from "./runtime-client.js";
import { workspacePreviewUrl } from "./workspace-preview.js";

test("workspace preview returns an empty state for a removed file", async () => {
  assert.equal(await workspacePreviewUrl(async () => {
    throw new RuntimeRequestError({ code: -32000, message: "missing", data: { code: "WORKSPACE_FILE_NOT_FOUND" } });
  }), null);
});

test("workspace preview preserves successful URLs", async () => {
  assert.equal(await workspacePreviewUrl(async () => "eidos-preview://token/image.png"), "eidos-preview://token/image.png");
});

test("workspace preview propagates permission, identity, version and unexpected errors", async () => {
  for (const code of ["WORKSPACE_BOUNDARY_VIOLATION", "WORKSPACE_IDENTITY_CHANGED", "WORKSPACE_FILE_CHANGED", "INTERNAL_ERROR"]) {
    const error = new RuntimeRequestError({ code: -32000, message: code, data: { code } });
    await assert.rejects(workspacePreviewUrl(async () => { throw error; }), (actual) => actual === error);
  }
  const error = new Error("unexpected");
  await assert.rejects(workspacePreviewUrl(async () => { throw error; }), (actual) => actual === error);
});
