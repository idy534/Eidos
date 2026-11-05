import test from "node:test";
import assert from "node:assert/strict";
import { link, mkdir, mkdtemp, realpath, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { clipboardFilePaths, MAX_PASTED_IMAGE_BYTES, readPastedImageFile } from "./clipboard-image.js";

async function fixture(): Promise<{ root: string; data: string; cleanup: () => Promise<void> }> {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), "eidos-clipboard-image-")));
  const data = path.join(root, "data");
  await mkdir(data);
  return { root, data, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test("clipboardFilePaths reads the filename list and the file URL", () => {
  const formats: Record<string, string> = {
    NSFilenamesPboardType: `<?xml version="1.0"?><plist><array><string>/Users/me/A &amp; B.jpg</string><string>relative.png</string><string>/tmp/&#x4E2D;.png</string></array></plist>`,
    "public.file-url": `${pathToFileURL("/Users/me/微信 图片.jpg").href}\n`,
  };
  const paths = clipboardFilePaths((format) => formats[format] ?? "");
  assert.deepEqual(paths, ["/Users/me/A & B.jpg", "/tmp/中.png", "/Users/me/微信 图片.jpg"]);
});

test("clipboardFilePaths tolerates missing formats and reader errors", () => {
  assert.deepEqual(clipboardFilePaths(() => ""), []);
  assert.deepEqual(clipboardFilePaths(() => { throw new Error("unsupported"); }), []);
  assert.deepEqual(clipboardFilePaths((format) => format === "public.file-url" ? "file://%E0%A4%A" : ""), []);
});

test("readPastedImageFile reads a listed file, including a hard-linked one", async () => {
  const { root, data, cleanup } = await fixture();
  try {
    const source = path.join(root, "copied.JPG");
    await writeFile(source, Buffer.from([1, 2, 3]));
    await link(source, path.join(root, "other-link.jpg"));
    const result = await readPastedImageFile(source, { clipboardPaths: [source], dataDirectory: data });
    assert.deepEqual([...result.bytes], [1, 2, 3]);
    assert.equal(result.extension, ".jpg");
  } finally {
    await cleanup();
  }
});

test("readPastedImageFile only reads files the clipboard lists", async () => {
  const { root, data, cleanup } = await fixture();
  try {
    const source = path.join(root, "copied.png");
    await writeFile(source, "x");
    await assert.rejects(readPastedImageFile(source, { clipboardPaths: [], dataDirectory: data }), /剪贴板中没有这个文件/);
    await assert.rejects(readPastedImageFile("copied.png", { clipboardPaths: ["copied.png"], dataDirectory: data }), /图片路径无效/);
    await assert.rejects(readPastedImageFile(42, { clipboardPaths: [], dataDirectory: data }), /图片路径无效/);
    await assert.rejects(readPastedImageFile(path.join(root, "absent.png"), { clipboardPaths: [], dataDirectory: data }), /剪贴板中没有这个文件/);
    // The pasteboard and the renderer may spell the same file through a symlinked directory.
    const linkedDirectory = path.join(root, "linked");
    await symlink(root, linkedDirectory);
    const result = await readPastedImageFile(path.join(linkedDirectory, "copied.png"), { clipboardPaths: [source], dataDirectory: data });
    assert.equal(result.bytes.toString(), "x");
  } finally {
    await cleanup();
  }
});

test("readPastedImageFile refuses the Eidos data directory, also through a symlink", async () => {
  const { root, data, cleanup } = await fixture();
  try {
    const inside = path.join(data, "blobs", "image.png");
    await mkdir(path.dirname(inside), { recursive: true });
    await writeFile(inside, "x");
    const alias = path.join(root, "alias.png");
    await symlink(inside, alias);
    for (const source of [inside, alias]) {
      await assert.rejects(readPastedImageFile(source, { clipboardPaths: [source], dataDirectory: data }), /数据目录/);
    }
  } finally {
    await cleanup();
  }
});

test("readPastedImageFile rejects directories and oversized files", async () => {
  const { root, data, cleanup } = await fixture();
  try {
    const directory = path.join(root, "folder.png");
    await mkdir(directory);
    await assert.rejects(readPastedImageFile(directory, { clipboardPaths: [directory], dataDirectory: data }));
    const large = path.join(root, "large.png");
    await writeFile(large, Buffer.alloc(MAX_PASTED_IMAGE_BYTES + 1));
    await assert.rejects(readPastedImageFile(large, { clipboardPaths: [large], dataDirectory: data }), /10 MiB/);
  } finally {
    await cleanup();
  }
});
