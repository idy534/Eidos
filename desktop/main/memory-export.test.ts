import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { exportMemoryNotes } from "./memory-export.js";

test("Markdown export streams every page and publishes only the complete file", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "eidos-export-test-"));
  try {
    const destination = path.join(directory, "notes.md");
    const seen: number[] = [];
    await exportMemoryNotes(destination, { scope: "global" }, async (request) => {
      seen.push(request.cursor!);
      return request.cursor === 0
        ? { generation: 2, files: [{ path: "MEMORY.md", content: "index" }], nextCursor: 16 }
        : { generation: 2, files: [{ path: "last.md", content: "最后一页" }], nextCursor: null };
    });
    assert.deepEqual(seen, [0, 16]);
    assert.match(await readFile(destination, "utf8"), /index[\s\S]*最后一页/);
    assert.deepEqual(await readdir(directory), ["notes.md"]);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("a concurrent edit or invalid cursor preserves the existing export and removes staging files", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "eidos-export-test-"));
  try {
    const destination = path.join(directory, "notes.md");
    await writeFile(destination, "original");
    for (const changed of [true, false]) {
      await assert.rejects(exportMemoryNotes(destination, {}, async (request) => ({
        generation: request.cursor && changed ? 3 : 2,
        files: [{ path: "note.md", content: "partial" }], nextCursor: request.cursor ? 16 : 16,
      })));
      assert.equal(await readFile(destination, "utf8"), "original");
      assert.deepEqual(await readdir(directory), ["notes.md"]);
    }
  } finally { await rm(directory, { recursive: true, force: true }); }
});
