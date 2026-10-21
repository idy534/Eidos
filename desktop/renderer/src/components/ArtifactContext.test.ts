import assert from "node:assert/strict";
import test from "node:test";

import { displayFilePath, workspaceFileLink } from "./ArtifactContext.js";

test("resolves absolute workspace links and preserves line and column", () => {
  assert.deepEqual(
    workspaceFileLink("/workspace/My%20Project/src/%E4%B8%AD%E6%96%87.ts:42:5", "/workspace"),
    { path: "My Project/src/中文.ts", line: 42, column: 5 },
  );
  assert.deepEqual(
    workspaceFileLink("/workspace/src/app.ts#L19C3", "/workspace"),
    { path: "src/app.ts", line: 19, column: 3 },
  );
});

test("requires conversation links to stay inside the current execution root", () => {
  assert.deepEqual(
    workspaceFileLink("/workspace/first/index.ts", "/workspace"),
    { path: "first/index.ts" },
  );
  assert.deepEqual(
    workspaceFileLink("/workspace/second/index.ts", "/workspace"),
    { path: "second/index.ts" },
  );
  assert.equal(workspaceFileLink("src/app.ts:42", "/workspace"), undefined);
  assert.equal(workspaceFileLink("/outside/app.ts:42", "/workspace"), undefined);
  assert.equal(workspaceFileLink("/old-worktree/src/app.ts:42", "/current-worktree"), undefined);
  assert.deepEqual(
    workspaceFileLink("/current-worktree/src/app.ts:42", "/current-worktree"),
    { path: "src/app.ts", line: 42 },
  );
  assert.equal(workspaceFileLink("javascript:alert(1)", "/workspace"), undefined);
  assert.equal(workspaceFileLink("//host/share/app.ts", "/workspace"), undefined);
});

test("rejects malformed positions and ambiguous fragments", () => {
  assert.equal(workspaceFileLink("/workspace/app.ts:0", "/workspace"), undefined);
  assert.equal(workspaceFileLink("/workspace/app.ts:4:0", "/workspace"), undefined);
  assert.equal(workspaceFileLink("/workspace/app.ts:9007199254740992", "/workspace"), undefined);
  assert.equal(workspaceFileLink("/workspace/app.ts:4#L5", "/workspace"), undefined);
  assert.equal(workspaceFileLink("/workspace/app.ts#section", "/workspace"), undefined);
});

test("keeps document-relative and file links based on the displayed document", () => {
  assert.deepEqual(
    workspaceFileLink("../assets/diagram.png#L7C4", "/workspace", "docs/guides/README.md"),
    { path: "docs/assets/diagram.png", line: 7, column: 4 },
  );
  assert.deepEqual(
    workspaceFileLink("file:///workspace/docs/My%20Guide.md:3:2", "/workspace", "docs/README.md"),
    { path: "docs/My Guide.md", line: 3, column: 2 },
  );
  assert.equal(
    workspaceFileLink("file:///workspace/docs/Guide.md", "/workspace"),
    undefined,
  );
  assert.equal(
    workspaceFileLink("../../../outside.txt", "/workspace", "docs/guides/README.md"),
    undefined,
  );
});

test("displayFilePath prioritizes absolute path when execution root is absolute and falls back to relative path", () => {
  assert.equal(
    displayFilePath("夏威夷旅行见闻.pptx", "/Users/xielei/Documents/test1/Office-Test"),
    "/Users/xielei/Documents/test1/Office-Test/夏威夷旅行见闻.pptx",
  );
  assert.equal(
    displayFilePath("src/app.ts", "/workspace", { line: 42, column: 5 }),
    "/workspace/src/app.ts:42:5",
  );
  assert.equal(
    displayFilePath("/already/absolute/path.txt", "/workspace"),
    "/already/absolute/path.txt",
  );
  assert.equal(
    displayFilePath("夏威夷旅行见闻.pptx"),
    "夏威夷旅行见闻.pptx",
  );
  assert.equal(
    displayFilePath("夏威夷旅行见闻.pptx", "relative-root"),
    "夏威夷旅行见闻.pptx",
  );
});
