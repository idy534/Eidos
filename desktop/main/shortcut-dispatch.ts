import type { BrowserWindow } from "electron";
import { IPC } from "../shared/ipc-channels.js";

export interface ShortcutManagerDeps {
  register: (accelerator: string, callback: () => void) => boolean;
  unregisterAll: () => void;
  getFocusedWindow: () => BrowserWindow | null;
}

export function registerAppShortcuts(deps: ShortcutManagerDeps): void {
  deps.register("CommandOrControl+N", () => {
    dispatchShortcutToFocusedWindow(deps.getFocusedWindow(), IPC.APP_NEW_TASK);
  });

  deps.register("CommandOrControl+O", () => {
    dispatchShortcutToFocusedWindow(deps.getFocusedWindow(), IPC.APP_OPEN_WORKSPACE);
  });
}

export function dispatchShortcutToFocusedWindow(
  window: BrowserWindow | null,
  channel: string,
): boolean {
  if (!window || window.isDestroyed()) return false;
  if (!window.webContents || window.webContents.isDestroyed()) return false;

  window.webContents.send(channel);
  return true;
}

export function unregisterAppShortcuts(deps: ShortcutManagerDeps): void {
  deps.unregisterAll();
}
