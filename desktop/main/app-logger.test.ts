import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { inflateRawSync } from "node:zlib";

import { exportDiagnosticBundle, RotatingFileLogger } from "./app-logger.js";

function parseZipEntries(zipBuffer: Buffer): Map<string, string> {
  const entries = new Map<string, string>();
  let offset = 0;
  while (offset + 4 <= zipBuffer.length && zipBuffer.readUInt32LE(offset) === 0x04034b50) {
    const method = zipBuffer.readUInt16LE(offset + 8);
    const compressedSize = zipBuffer.readUInt32LE(offset + 18);
    const nameLen = zipBuffer.readUInt16LE(offset + 26);
    const extraLen = zipBuffer.readUInt16LE(offset + 28);
    const nameStart = offset + 30;
    const dataStart = nameStart + nameLen + extraLen;
    const dataEnd = dataStart + compressedSize;
    const name = zipBuffer.subarray(nameStart, nameStart + nameLen).toString("utf8");
    const rawPayload = zipBuffer.subarray(dataStart, dataEnd);
    const content = method === 8 ? inflateRawSync(rawPayload) : rawPayload;
    entries.set(name, content.toString("utf8"));
    offset = dataEnd;
  }
  return entries;
}

test("RotatingFileLogger redacts secrets and rotates files at size boundary", () => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "eidos-logger-"));
  try {
    const logger = new RotatingFileLogger(tempRoot, { maxBytes: 300, maxFiles: 3 });
    for (let i = 0; i < 12; i += 1) {
      logger.append(
        "desktop.log",
        `entry=${i} Authorization: Bearer sk-secret-${String(i).padStart(10, "0")} apiKey=sk-1234567890abcdef`,
      );
    }

    const active = readFileSync(path.join(tempRoot, "desktop.log"), "utf8");
    assert.ok(active.includes("[REDACTED]"));
    assert.equal(active.includes("sk-secret-0000"), false);
    assert.equal(active.includes("sk-1234567890abcdef"), false);
    assert.equal(existsSync(path.join(tempRoot, "desktop.log.1")), true);
    assert.equal(existsSync(path.join(tempRoot, "desktop.log.2")), true);
    assert.equal(existsSync(path.join(tempRoot, "desktop.log.3")), false);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("exportDiagnosticBundle packages redacted logs, summary, and dump inventory into valid ZIP", () => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "eidos-diag-"));
  try {
    const logDir = path.join(tempRoot, "logs");
    const crashDir = path.join(tempRoot, "crash-dumps");
    const destinationZip = path.join(tempRoot, "out", "diagnostics.zip");
    mkdirSync(logDir, { recursive: true });
    mkdirSync(crashDir, { recursive: true });

    writeFileSync(path.join(logDir, "desktop.log"), "line1 Authorization: Bearer ghp_123456789012345678901234567890123456\n");
    writeFileSync(path.join(logDir, "runtime.log"), "stderr token=sk-abc1234567890\n");
    writeFileSync(path.join(crashDir, "dump-001.dmp"), Buffer.from("RAW-MINIDUMP-SECRET"));

    exportDiagnosticBundle({
      destinationPath: destinationZip,
      logDirectory: logDir,
      crashDumpsDirectory: crashDir,
      summary: {
        appVersion: "0.5.7",
        apiKey: "should-be-redacted",
        runtimeStatus: { state: "ready" },
      },
    });

    const parsed = parseZipEntries(readFileSync(destinationZip));
    assert.deepEqual([...parsed.keys()].sort(), ["desktop.log", "runtime.log", "summary.json"]);

    const summary = JSON.parse(parsed.get("summary.json")!);
    assert.equal(summary.appVersion, "0.5.7");
    assert.equal(summary.apiKey, "[REDACTED]");
    assert.equal(summary.crashDumps.length, 1);
    assert.equal(summary.crashDumps[0].name, "dump-001.dmp");

    assert.equal(parsed.get("desktop.log")!.includes("ghp_1234567890"), false);
    assert.equal(parsed.get("desktop.log")!.includes("[REDACTED]"), true);
    assert.equal(parsed.get("runtime.log")!.includes("sk-abc1234567890"), false);
    assert.equal(parsed.get("runtime.log")!.includes("RAW-MINIDUMP-SECRET"), false);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});
