import assert from "node:assert/strict";
import { mkdir, mkdtemp, realpath, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { resolveWorkspaceFileForOpen } from "./workspace-open.js";


test("resolves only a regular file inside the workspace", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "eidos-open-workspace-"));
  try {
    await mkdir(path.join(root, "src"));
    await writeFile(path.join(root, "src", "index.ts"), "export {};\n", "utf8");

    assert.equal(
      await resolveWorkspaceFileForOpen(root, "src/index.ts"),
      await realpath(path.join(root, "src", "index.ts")),
    );
    await assert.rejects(resolveWorkspaceFileForOpen(root, "../outside.ts"));
    await assert.rejects(resolveWorkspaceFileForOpen(root, "src"));
    await assert.rejects(
      resolveWorkspaceFileForOpen(root, "missing.txt"),
      (error: unknown) => error instanceof Error && error.message === "Workspace 文件不可用。",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("rejects a symlink that resolves outside the workspace", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "eidos-open-workspace-"));
  const outside = await mkdtemp(path.join(os.tmpdir(), "eidos-open-outside-"));
  try {
    await writeFile(path.join(outside, "secret.txt"), "secret\n", "utf8");
    await symlink(path.join(outside, "secret.txt"), path.join(root, "link.txt"));

    await assert.rejects(
      resolveWorkspaceFileForOpen(root, "link.txt"),
      /路径越界/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(outside, { recursive: true, force: true });
  }
});
