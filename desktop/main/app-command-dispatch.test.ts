import test from "node:test";
import assert from "node:assert/strict";
import {
  dispatchAppCommand,
  ensureAppWindow,
  type AppCommandDependencies,
  type AppCommandWindow,
} from "./app-command-dispatch.js";

function createMockWindow(options: {
  isDestroyed?: boolean;
  isMinimized?: boolean;
  isLoading?: boolean;
  wcDestroyed?: boolean;
} = {}): {
  window: AppCommandWindow;
  calls: {
    restore: number;
    show: number;
    focus: number;
    sent: string[];
  };
  triggerLoadFinish: () => void;
  triggerLoadFail: () => void;
} {
  const calls = {
    restore: 0,
    show: 0,
    focus: 0,
    sent: [] as string[],
  };

  let isDestroyed = options.isDestroyed ?? false;
  let isMinimized = options.isMinimized ?? false;
  let isLoading = options.isLoading ?? false;
  let wcDestroyed = options.wcDestroyed ?? false;

  const listeners: Record<string, ((...args: unknown[]) => void)[]> = {};

  const window: AppCommandWindow = {
    isDestroyed: () => isDestroyed,
    isMinimized: () => isMinimized,
    restore: () => {
      calls.restore++;
      isMinimized = false;
    },
    show: () => {
      calls.show++;
    },
    focus: () => {
      calls.focus++;
    },
    webContents: {
      isDestroyed: () => wcDestroyed,
      isLoading: () => isLoading,
      once: (event, listener) => {
        if (!listeners[event]) listeners[event] = [];
        listeners[event].push(listener);
      },
      removeListener: (event, listener) => {
        if (listeners[event]) {
          listeners[event] = listeners[event].filter((l) => l !== listener);
        }
      },
      send: (channel) => {
        calls.sent.push(channel);
      },
    },
  };

  return {
    window,
    calls,
    triggerLoadFinish: () => {
      isLoading = false;
      const list = listeners["did-finish-load"] || [];
      listeners["did-finish-load"] = [];
      for (const fn of list) fn();
    },
    triggerLoadFail: () => {
      isLoading = false;
      const list = listeners["did-fail-load"] || [];
      listeners["did-fail-load"] = [];
      for (const fn of list) fn();
    },
  };
}

test("ensureAppWindow reuses existing window, restores if minimized, shows and focuses", () => {
  const mock = createMockWindow({ isMinimized: true });
  const deps: AppCommandDependencies = {
    getExistingWindow: () => mock.window,
    createWindow: () => assert.fail("Should not create window"),
  };

  const win = ensureAppWindow(deps);
  assert.equal(win, mock.window);
  assert.equal(mock.calls.restore, 1);
  assert.equal(mock.calls.show, 1);
  assert.equal(mock.calls.focus, 1);
});

test("ensureAppWindow creates new window if no existing window", () => {
  const mock = createMockWindow();
  let created = false;
  const deps: AppCommandDependencies = {
    getExistingWindow: () => undefined,
    createWindow: () => {
      created = true;
      return mock.window;
    },
  };

  const win = ensureAppWindow(deps);
  assert.equal(win, mock.window);
  assert.equal(created, true);
});

test("dispatchAppCommand sends command immediately to loaded window", async () => {
  const mock = createMockWindow({ isLoading: false });
  const deps: AppCommandDependencies = {
    getExistingWindow: () => mock.window,
    createWindow: () => mock.window,
  };

  const result = await dispatchAppCommand("app:new-task", deps);
  assert.deepEqual(result, { status: "sent" });
  assert.deepEqual(mock.calls.sent, ["app:new-task"]);
});

test("dispatchAppCommand waits for did-finish-load if window is loading", async () => {
  const mock = createMockWindow({ isLoading: true });
  const deps: AppCommandDependencies = {
    getExistingWindow: () => mock.window,
    createWindow: () => mock.window,
  };

  const promise = dispatchAppCommand("app:open-workspace", deps);
  assert.deepEqual(mock.calls.sent, []);

  mock.triggerLoadFinish();

  const result = await promise;
  assert.deepEqual(result, { status: "sent" });
  assert.deepEqual(mock.calls.sent, ["app:open-workspace"]);
});

test("dispatchAppCommand returns load_failed on did-fail-load without sending command", async () => {
  const mock = createMockWindow({ isLoading: true });
  const deps: AppCommandDependencies = {
    getExistingWindow: () => mock.window,
    createWindow: () => mock.window,
  };

  const promise = dispatchAppCommand("app:new-task", deps);
  mock.triggerLoadFail();

  const result = await promise;
  assert.deepEqual(result, { status: "load_failed" });
  assert.deepEqual(mock.calls.sent, []);
});

test("dispatchAppCommand returns window_destroyed if window is destroyed before sending", async () => {
  const mock = createMockWindow({ isDestroyed: true });
  const deps: AppCommandDependencies = {
    getExistingWindow: () => mock.window,
    createWindow: () => mock.window,
  };

  const result = await dispatchAppCommand("app:new-task", deps);
  assert.deepEqual(result, { status: "window_destroyed" });
  assert.deepEqual(mock.calls.sent, []);
});

test("dispatchAppCommand returns window_destroyed if webContents is destroyed", async () => {
  const mock = createMockWindow({ wcDestroyed: true });
  const deps: AppCommandDependencies = {
    getExistingWindow: () => mock.window,
    createWindow: () => mock.window,
  };

  const result = await dispatchAppCommand("app:new-task", deps);
  assert.deepEqual(result, { status: "window_destroyed" });
  assert.deepEqual(mock.calls.sent, []);
});
