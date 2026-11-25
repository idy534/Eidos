import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildApprovalNotification,
  buildRunCompletedNotification,
  buildViewMenuSubmenu,
  DEFAULT_WINDOW_HEIGHT,
  DEFAULT_WINDOW_WIDTH,
  loadWindowState,
  MIN_WINDOW_HEIGHT,
  MIN_WINDOW_WIDTH,
  normalizeWindowBounds,
  saveWindowStateSync,
  shouldHideWindowToTray,
} from "./desktop-shell.js";

test("normalizeWindowBounds clamps minimum size and drops off-screen positions", () => {
  const displays = [{ x: 0, y: 0, width: 1920, height: 1080 }];
  assert.deepEqual(normalizeWindowBounds(null, displays), {
    width: DEFAULT_WINDOW_WIDTH,
    height: DEFAULT_WINDOW_HEIGHT,
  });

  assert.deepEqual(
    normalizeWindowBounds({ x: 100, y: 80, width: 400, height: 300, isMaximized: true }, displays),
    { x: 100, y: 80, width: MIN_WINDOW_WIDTH, height: MIN_WINDOW_HEIGHT, isMaximized: true },
  );

  assert.deepEqual(
    normalizeWindowBounds({ x: 5000, y: 5000, width: 1200, height: 900 }, displays),
    { width: 1200, height: 900 },
  );

  // Multi-monitor negative coordinates on left display remain valid when visible overlap exists.
  const multi = [
    { x: -1920, y: 0, width: 1920, height: 1080 },
    { x: 0, y: 0, width: 1920, height: 1080 },
  ];
  assert.deepEqual(
    normalizeWindowBounds({ x: -1600, y: 120, width: 1280, height: 820 }, multi),
    { x: -1600, y: 120, width: 1280, height: 820 },
  );
});

test("loadWindowState and saveWindowStateSync round-trip and tolerate corrupt JSON", () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), "eidos-window-state-"));
  try {
    const filePath = path.join(dir, "nested", "window-state.json");
    const displays = [{ x: 0, y: 0, width: 1920, height: 1080 }];

    assert.deepEqual(loadWindowState(filePath, displays), {
      width: DEFAULT_WINDOW_WIDTH,
      height: DEFAULT_WINDOW_HEIGHT,
    });

    saveWindowStateSync(filePath, { x: 120, y: 60, width: 1400, height: 900, isMaximized: false });
    assert.deepEqual(loadWindowState(filePath, displays), {
      x: 120,
      y: 60,
      width: 1400,
      height: 900,
    });

    writeFileSync(filePath, "{broken", "utf8");
    assert.deepEqual(loadWindowState(filePath, displays), {
      width: DEFAULT_WINDOW_WIDTH,
      height: DEFAULT_WINDOW_HEIGHT,
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("buildViewMenuSubmenu strips reload and toggleDevTools only when packaged", () => {
  const devRoles = buildViewMenuSubmenu(false).map((item) => item.role).filter(Boolean);
  assert.equal(devRoles.includes("reload"), true);
  assert.equal(devRoles.includes("forceReload"), true);
  assert.equal(devRoles.includes("toggleDevTools"), true);

  const prodRoles = buildViewMenuSubmenu(true).map((item) => item.role).filter(Boolean);
  assert.equal(prodRoles.includes("reload"), false);
  assert.equal(prodRoles.includes("forceReload"), false);
  assert.equal(prodRoles.includes("toggleDevTools"), false);
  assert.equal(prodRoles.includes("resetZoom"), true);
  assert.equal(prodRoles.includes("togglefullscreen"), true);
});

test("desktop notifications and tray close policy behave predictably", () => {
  assert.equal(buildRunCompletedNotification("succeeded")?.title, "Eidos 任务已完成");
  assert.equal(buildRunCompletedNotification("failed")?.title, "Eidos 任务失败");
  assert.equal(buildRunCompletedNotification("stopped")?.title, "Eidos 任务已暂停");
  assert.equal(buildRunCompletedNotification("canceled"), null);

  assert.deepEqual(buildApprovalNotification({ summary: "运行 npm test" }), {
    title: "Eidos 需要审批",
    body: "运行 npm test",
  });
  assert.deepEqual(buildApprovalNotification({}), {
    title: "Eidos 需要审批",
    body: "有一项操作正在等待你的确认。",
  });

  assert.equal(shouldHideWindowToTray({ platform: "win32", hasTray: true, quitCommitted: false }), true);
  assert.equal(shouldHideWindowToTray({ platform: "win32", hasTray: true, quitCommitted: true }), false);
  assert.equal(shouldHideWindowToTray({ platform: "win32", hasTray: true, quitCommitted: false, disableTrayEnv: true }), false);
  assert.equal(shouldHideWindowToTray({ platform: "darwin", hasTray: true, quitCommitted: false }), false);
  assert.equal(shouldHideWindowToTray({ platform: "win32", hasTray: false, quitCommitted: false }), false);
});
