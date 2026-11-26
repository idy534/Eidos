import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import { deflateRawSync } from "node:zlib";

import { redactLogLine, sanitizeLogValue } from "./log-redaction.js";

export const DEFAULT_MAX_LOG_BYTES = 5 * 1024 * 1024;
export const DEFAULT_MAX_LOG_FILES = 5;
export const MAX_DIAGNOSTIC_LOG_TAIL_BYTES = 1024 * 1024;
// `<data>/logs` belongs to the Runtime, which requires a private 0700 directory
// and fails storage closed otherwise. Desktop logs live beside it, never inside.
export const DESKTOP_LOG_DIRECTORY_NAME = "desktop-logs";
const PRIVATE_DIRECTORY_MODE = 0o700;
const PRIVATE_FILE_MODE = 0o600;

export function desktopLogDirectory(dataDirectory: string): string {
  return path.join(dataDirectory, DESKTOP_LOG_DIRECTORY_NAME);
}

export interface LoggerOptions {
  maxBytes?: number;
  maxFiles?: number;
}

export class RotatingFileLogger {
  private readonly logDirectory: string;
  private readonly maxBytes: number;
  private readonly maxFiles: number;

  constructor(logDirectory: string, options: LoggerOptions = {}) {
    this.logDirectory = logDirectory;
    this.maxBytes = Math.max(256, options.maxBytes ?? DEFAULT_MAX_LOG_BYTES);
    this.maxFiles = Math.max(1, options.maxFiles ?? DEFAULT_MAX_LOG_FILES);
  }

  append(fileName: "desktop.log" | "runtime.log", rawLine: string): void {
    try {
      mkdirSync(this.logDirectory, { recursive: true, mode: PRIVATE_DIRECTORY_MODE });
      const target = path.join(this.logDirectory, fileName);
      const safeLine = `${redactLogLine(rawLine.replace(/\r?\n$/, ""))}\n`;
      const encodedBytes = Buffer.byteLength(safeLine, "utf8");
      this.rotateIfNeeded(target, encodedBytes);
      appendFileSync(target, safeLine, { encoding: "utf8", mode: PRIVATE_FILE_MODE });
    } catch {
      // File logging is best-effort and must never disrupt the Main process.
    }
  }

  private rotateIfNeeded(target: string, incomingBytes: number): void {
    if (!existsSync(target)) return;
    const size = statSync(target).size;
    if (size + incomingBytes <= this.maxBytes) return;
    for (let index = this.maxFiles - 1; index >= 1; index -= 1) {
      const older = index === 1 ? target : `${target}.${index - 1}`;
      const destination = `${target}.${index}`;
      if (existsSync(older)) {
        if (existsSync(destination)) {
          rmSync(destination, { force: true });
        }
        renameSync(older, destination);
      }
    }
  }
}

export interface ZipEntry {
  name: string;
  data: Buffer;
}

const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = (c & 1) !== 0 ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer: Buffer): number {
  let crc = 0xffffffff;
  for (let i = 0; i < buffer.length; i += 1) {
    crc = CRC32_TABLE[(crc ^ buffer[i]!) & 0xff]! ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function buildZipBuffer(entries: readonly ZipEntry[]): Buffer {
  const localChunks: Buffer[] = [];
  const centralChunks: Buffer[] = [];
  let offset = 0;

  for (const entry of entries) {
    const nameBytes = Buffer.from(entry.name.replaceAll("\\", "/"), "utf8");
    const uncompressed = entry.data;
    const compressed = deflateRawSync(uncompressed);
    const useDeflate = compressed.length < uncompressed.length;
    const method = useDeflate ? 8 : 0;
    const payload = useDeflate ? compressed : uncompressed;
    const checksum = crc32(uncompressed);

    const localHeader = Buffer.alloc(30);
    localHeader.writeUInt32LE(0x04034b50, 0);
    localHeader.writeUInt16LE(20, 4); // version needed: 2.0
    localHeader.writeUInt16LE(0x0800, 6); // UTF-8 filename flag
    localHeader.writeUInt16LE(method, 8);
    localHeader.writeUInt16LE(0, 10);
    localHeader.writeUInt16LE(0, 12);
    localHeader.writeUInt32LE(checksum, 14);
    localHeader.writeUInt32LE(payload.length, 18);
    localHeader.writeUInt32LE(uncompressed.length, 22);
    localHeader.writeUInt16LE(nameBytes.length, 26);
    localHeader.writeUInt16LE(0, 28);

    localChunks.push(localHeader, nameBytes, payload);

    const centralHeader = Buffer.alloc(46);
    centralHeader.writeUInt32LE(0x02014b50, 0);
    centralHeader.writeUInt16LE(20, 4); // version made by
    centralHeader.writeUInt16LE(20, 6); // version needed
    centralHeader.writeUInt16LE(0x0800, 8); // UTF-8 flag
    centralHeader.writeUInt16LE(method, 10);
    centralHeader.writeUInt16LE(0, 12);
    centralHeader.writeUInt16LE(0, 14);
    centralHeader.writeUInt32LE(checksum, 16);
    centralHeader.writeUInt32LE(payload.length, 20);
    centralHeader.writeUInt32LE(uncompressed.length, 24);
    centralHeader.writeUInt16LE(nameBytes.length, 28);
    centralHeader.writeUInt16LE(0, 30);
    centralHeader.writeUInt16LE(0, 32);
    centralHeader.writeUInt16LE(0, 34);
    centralHeader.writeUInt16LE(0, 36);
    centralHeader.writeUInt32LE(0, 38);
    centralHeader.writeUInt32LE(offset, 42);

    centralChunks.push(centralHeader, nameBytes);
    offset += localHeader.length + nameBytes.length + payload.length;
  }

  const centralDirectorySize = centralChunks.reduce((sum, buf) => sum + buf.length, 0);
  const endRecord = Buffer.alloc(22);
  endRecord.writeUInt32LE(0x06054b50, 0);
  endRecord.writeUInt16LE(0, 4);
  endRecord.writeUInt16LE(0, 6);
  endRecord.writeUInt16LE(entries.length, 8);
  endRecord.writeUInt16LE(entries.length, 10);
  endRecord.writeUInt32LE(centralDirectorySize, 12);
  endRecord.writeUInt32LE(offset, 16);
  endRecord.writeUInt16LE(0, 20);

  return Buffer.concat([...localChunks, ...centralChunks, endRecord]);
}

function readRedactedTail(filePath: string, maxBytes = MAX_DIAGNOSTIC_LOG_TAIL_BYTES): string {
  if (!existsSync(filePath)) return "";
  const raw = readFileSync(filePath);
  const sliced = raw.length > maxBytes ? raw.subarray(raw.length - maxBytes) : raw;
  return sliced
    .toString("utf8")
    .split(/\r?\n/)
    .map((line) => redactLogLine(line))
    .join("\n");
}

function listCrashDumps(crashDumpsDirectory?: string): Array<{ name: string; size: number; modifiedAt: string }> {
  if (!crashDumpsDirectory || !existsSync(crashDumpsDirectory)) return [];
  try {
    return readdirSync(crashDumpsDirectory, { withFileTypes: true })
      .filter((entry) => entry.isFile())
      .slice(0, 64)
      .map((entry) => {
        const stats = statSync(path.join(crashDumpsDirectory, entry.name));
        return {
          name: entry.name,
          size: stats.size,
          modifiedAt: stats.mtime.toISOString(),
        };
      });
  } catch {
    return [];
  }
}

export interface ExportDiagnosticsOptions {
  destinationPath: string;
  logDirectory: string;
  crashDumpsDirectory?: string;
  summary: Record<string, unknown>;
}

export function exportDiagnosticBundle(options: ExportDiagnosticsOptions): void {
  const safeSummary = sanitizeLogValue({
    exportedAt: new Date().toISOString(),
    ...options.summary,
    crashDumps: listCrashDumps(options.crashDumpsDirectory),
  });
  const entries: ZipEntry[] = [
    {
      name: "summary.json",
      data: Buffer.from(`${JSON.stringify(safeSummary, null, 2)}\n`, "utf8"),
    },
    {
      name: "desktop.log",
      data: Buffer.from(readRedactedTail(path.join(options.logDirectory, "desktop.log")), "utf8"),
    },
    {
      name: "runtime.log",
      data: Buffer.from(readRedactedTail(path.join(options.logDirectory, "runtime.log")), "utf8"),
    },
  ];
  mkdirSync(path.dirname(options.destinationPath), { recursive: true });
  const zipBytes = buildZipBuffer(entries);
  const tempPath = `${options.destinationPath}.tmp-${process.pid}-${Date.now()}`;
  writeFileSync(tempPath, zipBytes);
  renameSync(tempPath, options.destinationPath);
}
