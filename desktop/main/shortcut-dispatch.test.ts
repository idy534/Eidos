import assert from "node:assert/strict";
import test from "node:test";
import {
  registerAppShortcuts,
  dispatchShortcutToFocusedWindow,
  unregisterAppShortcuts,
  type ShortcutManagerDeps,
} from "./shortcut-dispatch.js";
import { IPC } from "../shared/ipc-channels.js";
import type { BrowserWindow } from "electron";

function createMockWindow(destroyed = false, webContentsDestroyed = false) {
  const sent: string[] = [];
  const win = {
    isDestroyed: () => destroyed,
    webContents: {
      isDestroyed: () => webContentsDestroyed,
      send: (channel: string) => {
        sent.push(channel);
      },
    },
  } as unknown as BrowserWindow;
  return { win, sent };
}

test("Registers CommandOrControl+N and CommandOrControl+O shortcuts", () => {
  const registered: Record<string, () => void> = {};
  const deps: ShortcutManagerDeps = {
    register: (acc, cb) => {
      registered[acc] = cb;
      return true;
    },
    unregisterAll: () => {},
    getFocusedWindow: () => null,
  };

  registerAppShortcuts(deps);

  assert.deepEqual(Object.keys(registered), ["CommandOrControl+N", "CommandOrControl+O"]);
});

test("Dispatches IPC.APP_NEW_TASK and IPC.APP_OPEN_WORKSPACE to focused window", () => {
  const registered: Record<string, () => void> = {};
  const { win, sent } = createMockWindow();
  const deps: ShortcutManagerDeps = {
    register: (acc, cb) => {
      registered[acc] = cb;
      return true;
    },
    unregisterAll: () => {},
    getFocusedWindow: () => win,
  };

  registerAppShortcuts(deps);

  registered["CommandOrControl+N"]?.();
  assert.deepEqual(sent, [IPC.APP_NEW_TASK]);

  registered["CommandOrControl+O"]?.();
  assert.deepEqual(sent, [IPC.APP_NEW_TASK, IPC.APP_OPEN_WORKSPACE]);
});

test("Skips dispatch if window is null, destroyed, or webContents is destroyed", () => {
  assert.equal(dispatchShortcutToFocusedWindow(null, IPC.APP_NEW_TASK), false);

  const { win: destroyedWin } = createMockWindow(true, false);
  assert.equal(dispatchShortcutToFocusedWindow(destroyedWin, IPC.APP_NEW_TASK), false);

  const { win: destroyedWebContentsWin } = createMockWindow(false, true);
  assert.equal(dispatchShortcutToFocusedWindow(destroyedWebContentsWin, IPC.APP_NEW_TASK), false);
});

test("Unregisters all shortcuts on application cleanup", () => {
  let unregistered = false;
  const deps: ShortcutManagerDeps = {
    register: () => true,
    unregisterAll: () => { unregistered = true; },
    getFocusedWindow: () => null,
  };

  unregisterAppShortcuts(deps);
  assert.equal(unregistered, true);
});
