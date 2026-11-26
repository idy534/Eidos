import { randomUUID } from "node:crypto";
import { existsSync } from "node:fs";
import { rename, rm } from "node:fs/promises";
import path from "node:path";

import type { RuntimeStatus } from "../shared/index.js";

export interface DataRestoreDependencies {
  archivePath: string;
  dataDirectory: string;
  activeRunCount: number;
  runOfflineRestore: (archivePath: string, destinationDirectory: string) => Promise<void>;
  shutdownRuntime: () => Promise<void>;
  startRuntime: () => Promise<RuntimeStatus>;
  isStorageReady: (status: RuntimeStatus) => boolean;
}

export async function restoreDataBackup(dependencies: DataRestoreDependencies): Promise<RuntimeStatus> {
  if (dependencies.activeRunCount > 0) {
    throw new Error("当前仍有活动任务正在执行，请先停止任务后再恢复备份。");
  }

  const normalizedDataDir = path.resolve(dependencies.dataDirectory);
  const token = randomUUID().slice(0, 8);
  const stagingDir = `${normalizedDataDir}.restore-staging-${token}`;
  const previousDir = `${normalizedDataDir}.pre-restore-${Date.now()}-${token}`;

  // Stage offline restore before shutting down Runtime or moving live data.
  try {
    await dependencies.runOfflineRestore(dependencies.archivePath, stagingDir);
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true }).catch(() => undefined);
    throw error;
  }

  await dependencies.shutdownRuntime();

  let movedLiveToBackup = false;
  try {
    if (existsSync(normalizedDataDir)) {
      await rename(normalizedDataDir, previousDir);
      movedLiveToBackup = true;
    }
    await rename(stagingDir, normalizedDataDir);
    const status = await dependencies.startRuntime();
    if (!dependencies.isStorageReady(status)) {
      throw new Error(
        status.state === "error"
          ? status.message
          : "恢复后存储引擎未处于就绪状态",
      );
    }
    if (movedLiveToBackup) {
      await rm(previousDir, { recursive: true, force: true }).catch(() => undefined);
    }
    return status;
  } catch (error) {
    await rm(stagingDir, { recursive: true, force: true }).catch(() => undefined);
    if (movedLiveToBackup) {
      await rm(normalizedDataDir, { recursive: true, force: true }).catch(() => undefined);
      await rename(previousDir, normalizedDataDir).catch(() => undefined);
    }
    await dependencies.startRuntime().catch(() => undefined);
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`备份恢复失败，已自动回滚原数据目录：${detail}`);
  }
}
