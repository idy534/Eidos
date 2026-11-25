import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { restoreDataBackup } from "./data-restore.js";

test("restoreDataBackup stages offline archive, swaps data directory, and restarts Runtime", async () => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "eidos-restore-ok-"));
  try {
    const dataDir = path.join(tempRoot, "data");
    mkdirSync(dataDir, { recursive: true });
    writeFileSync(path.join(dataDir, "marker.txt"), "old-data", "utf8");

    const calls: string[] = [];
    const status = await restoreDataBackup({
      archivePath: path.join(tempRoot, "backup.zip"),
      dataDirectory: dataDir,
      activeRunCount: 0,
      runOfflineRestore: async (_archive, dest) => {
        calls.push("stage");
        mkdirSync(dest, { recursive: true });
        writeFileSync(path.join(dest, "marker.txt"), "restored-data", "utf8");
      },
      shutdownRuntime: async () => {
        calls.push("shutdown");
      },
      startRuntime: async () => {
        calls.push("start");
        return {
          state: "ready",
          runtimeVersion: "0.5.7",
          protocolVersion: 1,
          runShell: true,
          modelConfigured: true,
          storageHealth: { state: "ready" },
        };
      },
      isStorageReady: (s) => s.state === "ready" && s.storageHealth.state === "ready",
    });

    assert.equal(status.state, "ready");
    assert.deepEqual(calls, ["stage", "shutdown", "start"]);
    assert.equal(readFileSync(path.join(dataDir, "marker.txt"), "utf8"), "restored-data");
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("restoreDataBackup aborts before shutting down Runtime when archive staging fails", async () => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "eidos-restore-bad-"));
  try {
    const dataDir = path.join(tempRoot, "data");
    mkdirSync(dataDir, { recursive: true });
    writeFileSync(path.join(dataDir, "marker.txt"), "intact-data", "utf8");

    let shutdownCalled = false;
    await assert.rejects(
      restoreDataBackup({
        archivePath: path.join(tempRoot, "corrupt.zip"),
        dataDirectory: dataDir,
        activeRunCount: 0,
        runOfflineRestore: async (_archive, dest) => {
          mkdirSync(dest, { recursive: true });
          throw new Error("corrupt archive");
        },
        shutdownRuntime: async () => {
          shutdownCalled = true;
        },
        startRuntime: async () => ({ state: "starting" }),
        isStorageReady: () => true,
      }),
      /corrupt archive/,
    );

    assert.equal(shutdownCalled, false);
    assert.equal(readFileSync(path.join(dataDir, "marker.txt"), "utf8"), "intact-data");
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("restoreDataBackup rolls back to the original data directory if restarted storage is not ready", async () => {
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "eidos-restore-rollback-"));
  try {
    const dataDir = path.join(tempRoot, "data");
    mkdirSync(dataDir, { recursive: true });
    writeFileSync(path.join(dataDir, "marker.txt"), "original-live-data", "utf8");

    let startCount = 0;
    await assert.rejects(
      restoreDataBackup({
        archivePath: path.join(tempRoot, "incompatible.zip"),
        dataDirectory: dataDir,
        activeRunCount: 0,
        runOfflineRestore: async (_archive, dest) => {
          mkdirSync(dest, { recursive: true });
          writeFileSync(path.join(dest, "marker.txt"), "bad-restored-data", "utf8");
        },
        shutdownRuntime: async () => {},
        startRuntime: async () => {
          startCount += 1;
          if (startCount === 1) {
            return { state: "error", message: "schema corrupted" };
          }
          return {
            state: "ready",
            runtimeVersion: "0.5.7",
            protocolVersion: 1,
            runShell: true,
            modelConfigured: false,
            storageHealth: { state: "ready" },
          };
        },
        isStorageReady: (s) => s.state === "ready" && s.storageHealth.state === "ready",
      }),
      /已自动回滚原数据目录.*schema corrupted/,
    );

    assert.equal(startCount, 2);
    assert.equal(existsSync(dataDir), true);
    assert.equal(readFileSync(path.join(dataDir, "marker.txt"), "utf8"), "original-live-data");
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});
