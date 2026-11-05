import { constants } from "node:fs";
import { open, realpath, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const MAX_PASTED_IMAGE_BYTES = 10 * 1024 * 1024;

/**
 * Absolute paths of the files the macOS pasteboard currently lists.
 * `read` is Electron's `clipboard.read`; a format the pasteboard lacks reads as "".
 */
export function clipboardFilePaths(read: (format: string) => string): string[] {
  const found = new Set<string>();
  const filenames = readFormat(read, "NSFilenamesPboardType");
  for (const match of filenames.matchAll(/<string>([^<]*)<\/string>/g)) {
    const value = decodeXmlText(match[1] ?? "");
    if (path.isAbsolute(value)) found.add(path.normalize(value));
  }
  const url = readFormat(read, "public.file-url").trim();
  if (url.startsWith("file://")) {
    try {
      found.add(path.normalize(fileURLToPath(url)));
    } catch {
      // A malformed URL adds nothing.
    }
  }
  return [...found];
}

/**
 * Read the bytes of a pasted image file so Main can hand the Runtime a private
 * copy. Only a file the pasteboard itself lists is read, never one inside the
 * Eidos data directory, and the opened file must be the one that was checked.
 */
export async function readPastedImageFile(
  source: unknown,
  options: { clipboardPaths: readonly string[]; dataDirectory: string },
): Promise<{ bytes: Buffer; extension: string }> {
  if (typeof source !== "string" || !source || source.length > 4096 || source.includes("\0") || !path.isAbsolute(source)) {
    throw new Error("图片路径无效。");
  }
  // Compare resolved paths so /tmp and /private/tmp spellings of one file match.
  // A path that is not listed fails the same way whether or not it exists.
  const resolved = await realpath(path.normalize(source)).catch(() => undefined);
  const listed = await Promise.all(options.clipboardPaths.map((value) => realpath(value).catch(() => undefined)));
  if (resolved === undefined || !listed.includes(resolved)) throw new Error("剪贴板中没有这个文件。");
  const data = await realpath(options.dataDirectory).catch(() => path.resolve(options.dataDirectory));
  if (resolved === data || resolved.startsWith(data + path.sep)) {
    throw new Error("不能粘贴 Eidos 数据目录中的文件。");
  }
  const checked = await stat(resolved);
  const handle = await open(resolved, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.dev !== checked.dev || opened.ino !== checked.ino) {
      throw new Error("图片文件已变化，请重新复制。");
    }
    if (opened.size > MAX_PASTED_IMAGE_BYTES) throw new Error("图片不能超过 10 MiB。");
    const bytes = Buffer.alloc(opened.size);
    let offset = 0;
    while (offset < opened.size) {
      const { bytesRead } = await handle.read(bytes, offset, opened.size - offset, offset);
      if (bytesRead === 0) throw new Error("图片文件已变化，请重新复制。");
      offset += bytesRead;
    }
    const extension = path.extname(resolved).toLowerCase();
    return { bytes, extension: /^\.[a-z0-9]{1,8}$/.test(extension) ? extension : ".img" };
  } finally {
    await handle.close();
  }
}

function readFormat(read: (format: string) => string, format: string): string {
  try {
    const value = read(format);
    return typeof value === "string" ? value : "";
  } catch {
    return "";
  }
}

function decodeXmlText(value: string): string {
  return value.replace(/&(amp|lt|gt|quot|apos|#(\d{1,7})|#x([0-9a-fA-F]{1,6}));/g, (entity, name: string, decimal?: string, hex?: string) => {
    if (decimal) return safeCodePoint(Number(decimal), entity);
    if (hex) return safeCodePoint(Number.parseInt(hex, 16), entity);
    return ({ amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'" } as Record<string, string>)[name] ?? entity;
  });
}

function safeCodePoint(value: number, fallback: string): string {
  return value > 0 && value <= 0x10ffff ? String.fromCodePoint(value) : fallback;
}
