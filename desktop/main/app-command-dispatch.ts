import type { AppShortcut } from "../shared/index.js";

export interface AppCommandWindow {
  isDestroyed(): boolean;
  isMinimized(): boolean;
  restore(): void;
  show(): void;
  focus(): void;
  webContents: {
    isDestroyed(): boolean;
    isLoading(): boolean;
    once(
      event: "did-finish-load" | "did-fail-load",
      listener: (...args: unknown[]) => void,
    ): void;
    removeListener(
      event: string,
      listener: (...args: unknown[]) => void,
    ): void;
    send(channel: string): void;
  };
}

export interface AppCommandDependencies {
  getExistingWindow(): AppCommandWindow | undefined;
  createWindow(): AppCommandWindow;
}

export function ensureAppWindow(
  deps: AppCommandDependencies,
): AppCommandWindow {
  const existing = deps.getExistingWindow();
  if (existing && !existing.isDestroyed()) {
    if (existing.isMinimized()) {
      existing.restore();
    }
    existing.show();
    existing.focus();
    return existing;
  }
  return deps.createWindow();
}

export async function dispatchAppCommand(
  command: AppShortcut,
  deps: AppCommandDependencies,
): Promise<
  | { status: "sent" }
  | { status: "load_failed" }
  | { status: "window_destroyed" }
> {
  const window = ensureAppWindow(deps);

  if (window.isDestroyed() || window.webContents.isDestroyed()) {
    return { status: "window_destroyed" };
  }

  if (window.webContents.isLoading()) {
    const loadSuccess = await new Promise<boolean>((resolve) => {
      const onFinish = () => {
        window.webContents.removeListener("did-fail-load", onFail);
        resolve(true);
      };
      const onFail = () => {
        window.webContents.removeListener("did-finish-load", onFinish);
        resolve(false);
      };

      window.webContents.once("did-finish-load", onFinish);
      window.webContents.once("did-fail-load", onFail);
    });

    if (!loadSuccess) {
      return { status: "load_failed" };
    }
  }

  if (window.isDestroyed() || window.webContents.isDestroyed()) {
    return { status: "window_destroyed" };
  }

  window.webContents.send(command);
  return { status: "sent" };
}
