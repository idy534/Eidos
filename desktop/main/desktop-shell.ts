import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { MenuItemConstructorOptions } from "electron";

export const DEFAULT_WINDOW_WIDTH = 1180;
export const DEFAULT_WINDOW_HEIGHT = 800;
export const MIN_WINDOW_WIDTH = 800;
export const MIN_WINDOW_HEIGHT = 600;
const MIN_VISIBLE_OVERLAP_PX = 80;

export interface WindowState {
  x?: number;
  y?: number;
  width: number;
  height: number;
  isMaximized?: boolean;
}

export interface DisplayWorkArea {
  x: number;
  y: number;
  width: number;
  height: number;
}

function hasVisibleOverlap(
  bounds: { x: number; y: number; width: number; height: number },
  displays: readonly DisplayWorkArea[],
): boolean {
  if (displays.length === 0) return true;
  return displays.some((display) => {
    const overlapX = Math.min(bounds.x + bounds.width, display.x + display.width) - Math.max(bounds.x, display.x);
    const overlapY = Math.min(bounds.y + bounds.height, display.y + display.height) - Math.max(bounds.y, display.y);
    return overlapX >= MIN_VISIBLE_OVERLAP_PX && overlapY >= MIN_VISIBLE_OVERLAP_PX;
  });
}

export function normalizeWindowBounds(
  raw: unknown,
  displays: readonly DisplayWorkArea[],
): WindowState {
  const record = raw && typeof raw === "object" && !Array.isArray(raw)
    ? (raw as Record<string, unknown>)
    : undefined;
  const rawWidth = typeof record?.width === "number" && Number.isFinite(record.width)
    ? Math.round(record.width)
    : DEFAULT_WINDOW_WIDTH;
  const rawHeight = typeof record?.height === "number" && Number.isFinite(record.height)
    ? Math.round(record.height)
    : DEFAULT_WINDOW_HEIGHT;
  const width = Math.max(MIN_WINDOW_WIDTH, rawWidth);
  const height = Math.max(MIN_WINDOW_HEIGHT, rawHeight);
  const isMaximized = record?.isMaximized === true;

  if (
    typeof record?.x === "number"
    && Number.isFinite(record.x)
    && typeof record?.y === "number"
    && Number.isFinite(record.y)
  ) {
    const x = Math.round(record.x);
    const y = Math.round(record.y);
    if (hasVisibleOverlap({ x, y, width, height }, displays)) {
      return { x, y, width, height, ...(isMaximized ? { isMaximized: true } : {}) };
    }
  }
  return { width, height, ...(isMaximized ? { isMaximized: true } : {}) };
}

export function loadWindowState(
  filePath: string,
  displays: readonly DisplayWorkArea[],
): WindowState {
  try {
    const text = readFileSync(filePath, "utf8");
    const parsed: unknown = JSON.parse(text);
    return normalizeWindowBounds(parsed, displays);
  } catch {
    return { width: DEFAULT_WINDOW_WIDTH, height: DEFAULT_WINDOW_HEIGHT };
  }
}

export function saveWindowStateSync(filePath: string, state: WindowState): void {
  const normalized: WindowState = {
    width: Math.max(MIN_WINDOW_WIDTH, Math.round(state.width)),
    height: Math.max(MIN_WINDOW_HEIGHT, Math.round(state.height)),
    ...(typeof state.x === "number" && Number.isFinite(state.x) ? { x: Math.round(state.x) } : {}),
    ...(typeof state.y === "number" && Number.isFinite(state.y) ? { y: Math.round(state.y) } : {}),
    ...(state.isMaximized ? { isMaximized: true } : {}),
  };
  try {
    mkdirSync(path.dirname(filePath), { recursive: true });
    const tempPath = `${filePath}.tmp`;
    writeFileSync(tempPath, JSON.stringify(normalized, null, 2), "utf8");
    renameSync(tempPath, filePath);
  } catch {
    // Window state persistence failure is non-fatal.
  }
}

export function buildViewMenuSubmenu(isPackaged: boolean): MenuItemConstructorOptions[] {
  const items: MenuItemConstructorOptions[] = [];
  if (!isPackaged) {
    items.push(
      { role: "reload" },
      { role: "forceReload" },
      { role: "toggleDevTools" },
      { type: "separator" },
    );
  }
  items.push(
    { role: "resetZoom" },
    { role: "zoomIn" },
    { role: "zoomOut" },
    { type: "separator" },
    { role: "togglefullscreen" },
  );
  return items;
}

export function buildRunCompletedNotification(status: string): { title: string; body: string } | null {
  switch (status) {
    case "succeeded":
      return { title: "Eidos 任务已完成", body: "运行已成功完成，点击查看结果。" };
    case "failed":
      return { title: "Eidos 任务失败", body: "运行遇到错误，点击查看详情。" };
    case "stopped":
    case "paused":
      return { title: "Eidos 任务已暂停", body: "运行已暂停，点击恢复或查看状态。" };
    default:
      return null;
  }
}

export function buildApprovalNotification(request: {
  title?: string;
  summary?: string;
  toolName?: string;
}): { title: string; body: string } {
  const detail = request.summary?.trim() || request.title?.trim() || request.toolName?.trim();
  return {
    title: "Eidos 需要审批",
    body: detail || "有一项操作正在等待你的确认。",
  };
}

export function shouldHideWindowToTray(opts: {
  platform: NodeJS.Platform;
  hasTray: boolean;
  quitCommitted: boolean;
  disableTrayEnv?: boolean;
}): boolean {
  return opts.platform !== "darwin" && opts.hasTray && !opts.quitCommitted && !opts.disableTrayEnv;
}
