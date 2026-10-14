import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { cleanRendererAssets } from "./clean-renderer-assets.mjs";

test("cleans stale Vite assets and preserves generated file icons", () => {
  const root = mkdtempSync(join(tmpdir(), "eidos-renderer-assets-"));
  const rendererOutputDirectory = join(root, "renderer");
  const oldAsset = join(rendererOutputDirectory, "assets", "old-hash.js");
  const fileIcon = join(rendererOutputDirectory, "file-icons", "icons", "folder.svg");

  try {
    mkdirSync(join(rendererOutputDirectory, "assets"), { recursive: true });
    mkdirSync(join(rendererOutputDirectory, "file-icons", "icons"), { recursive: true });
    writeFileSync(oldAsset, "old Vite output");
    writeFileSync(fileIcon, "generated icon");

    cleanRendererAssets(rendererOutputDirectory);

    assert.equal(existsSync(oldAsset), false);
    assert.equal(readFileSync(fileIcon, "utf8"), "generated icon");
  } finally {
    rmSync(root, { force: true, recursive: true });
  }
});
