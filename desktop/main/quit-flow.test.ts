import assert from "node:assert/strict";
import test from "node:test";
import { QuitFlowManager, type QuitFlowDeps, type ActiveRunInfo } from "./quit-flow.js";
import type { BrowserWindow, MessageBoxOptions } from "electron";

const mockActiveRun: ActiveRunInfo = {
  id: "run-1",
  sessionId: "session-1",
  status: "running",
};

test("Zero active Runs allows quit immediately and triggers graceful shutdown", async () => {
  const manager = new QuitFlowManager();
  let gracefulCalled = false;

  const deps: QuitFlowDeps = {
    getActiveRuns: async () => [],
    showMessageBox: async () => ({ response: 1, checkboxChecked: false }),
    gracefulShutdown: async () => { gracefulCalled = true; },
    quitApp: () => {},
  };

  const decision = await manager.evaluateQuit(null, deps);

  assert.equal(decision, "quit_immediately");
  assert.equal(gracefulCalled, true);
  assert.equal(manager.quitting, true);
});

test("One or more active Runs presents confirmation dialog", async () => {
  const manager = new QuitFlowManager();
  let boxOpts: MessageBoxOptions | undefined;
  let gracefulCalled = false;

  const deps: QuitFlowDeps = {
    getActiveRuns: async () => [mockActiveRun],
    showMessageBox: async (_win, opts) => {
      boxOpts = opts;
      return { response: 1, checkboxChecked: false };
    },
    gracefulShutdown: async () => { gracefulCalled = true; },
    quitApp: () => {},
  };

  const decision = await manager.evaluateQuit(null, deps);

  assert.ok(boxOpts);
  assert.equal(boxOpts?.type, "warning");
  assert.deepEqual(boxOpts?.buttons, ["终止任务并退出", "取消"]);
  assert.ok(boxOpts?.message.includes("尚有 1 个运行中的 Agent 任务"));
  assert.equal(decision, "cancel_quit");
  assert.equal(gracefulCalled, false);
  assert.equal(manager.quitting, false);
});

test("Dialog response Confirm (index 0) proceeds with Graceful Shutdown", async () => {
  const manager = new QuitFlowManager();
  let gracefulCalled = false;

  const deps: QuitFlowDeps = {
    getActiveRuns: async () => [mockActiveRun],
    showMessageBox: async () => ({ response: 0, checkboxChecked: false }),
    gracefulShutdown: async () => { gracefulCalled = true; },
    quitApp: () => {},
  };

  const decision = await manager.evaluateQuit(null, deps);

  assert.equal(decision, "confirm_quit");
  assert.equal(gracefulCalled, true);
  assert.equal(manager.quitting, true);
});

test("Dialog failure fallback aborts quit safely", async () => {
  const manager = new QuitFlowManager();

  const deps: QuitFlowDeps = {
    getActiveRuns: async () => { throw new Error("RPC Timeout"); },
    showMessageBox: async () => ({ response: 0, checkboxChecked: false }),
    gracefulShutdown: async () => {},
    quitApp: () => {},
  };

  const decision = await manager.evaluateQuit(null, deps);

  assert.equal(decision, "cancel_quit");
  assert.equal(manager.quitting, false);
});

test("Quit flag state machine prevents double-triggering on app.quit()", async () => {
  const manager = new QuitFlowManager();
  let shutdownCount = 0;

  const deps: QuitFlowDeps = {
    getActiveRuns: async () => [],
    showMessageBox: async () => ({ response: 0, checkboxChecked: false }),
    gracefulShutdown: async () => { shutdownCount++; },
    quitApp: () => {},
  };

  await manager.evaluateQuit(null, deps);
  assert.equal(manager.quitting, true);

  const secondDecision = await manager.evaluateQuit(null, deps);
  assert.equal(secondDecision, "quit_immediately");
  assert.equal(shutdownCount, 1);
});
