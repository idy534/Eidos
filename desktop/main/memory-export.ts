import { randomUUID } from "node:crypto";
import { open, rename, unlink } from "node:fs/promises";
import path from "node:path";
import type { MemoryExport, MemoryExportRequest } from "../shared/memory.generated.js";

export async function exportMemoryNotes(destination: string, request: MemoryExportRequest,
  readPage: (request: MemoryExportRequest) => Promise<MemoryExport>): Promise<void> {
  const staging = path.join(path.dirname(destination), `.eidos-export-${randomUUID()}`);
  const handle = await open(staging, "wx", 0o600);
  let cursor = 0;
  let generation: number | undefined;
  let bytes = 0;
  try {
    for (let page = 0; page < 10_000; page++) {
      const value = await readPage({ ...request, cursor });
      generation ??= value.generation;
      if (generation !== value.generation) throw new Error("导出期间记忆发生变化，请重试。");
      for (const file of value.files) {
        const chunk = `\n\n<!-- ${file.path.replaceAll("--", "")} -->\n\n${file.content}`;
        bytes += Buffer.byteLength(chunk);
        if (bytes > 1024 * 1024 * 1024) throw new Error("记忆导出超过大小上限。");
        await handle.writeFile(chunk, "utf8");
      }
      if (value.nextCursor == null) {
        await handle.sync(); await handle.close();
        await rename(staging, destination);
        return;
      }
      if (value.nextCursor <= cursor) throw new Error("记忆导出分页无效。");
      cursor = value.nextCursor;
    }
    throw new Error("记忆导出超过分页上限。");
  } finally {
    await handle.close().catch(() => {});
    await unlink(staging).catch((error: NodeJS.ErrnoException) => { if (error.code !== "ENOENT") throw error; });
  }
}
