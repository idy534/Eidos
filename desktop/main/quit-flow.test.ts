import test from "node:test";
import assert from "node:assert/strict";
import {
  QuitFlowController,
  type ActiveRunProjection,
  type QuitFlowDependencies,
} from "./quit-flow.js";

function createHarness(options: {
  hasRuntime?: boolean;
  activeRunIds?: string[];
  dialogChoice?: "return_to_eidos" | "stop_and_exit";
  dialogReject?: boolean;
} = {}) {
  const calls = {
    preventDefault: 0,
    dialogShown: 0,
    canceledRunIds: [] as string[],
    shutdownCalls: 0,
    finalQuitCalls: 0,
    logs: [] as { level: string; msg: string; meta?: unknown }[],
  };

  let hasRuntime = options.hasRuntime ?? true;
  let runIds = [...(options.activeRunIds ?? [])];

  let dialogResolver: ((choice: "return_to_eidos" | "stop_and_exit") => void) | undefined;
  let dialogRejecter: ((err: Error) => void) | undefined;

  const projection: ActiveRunProjection = {
    runIds: () => [...runIds],
    count: () => runIds.length,
  };

  const deps: QuitFlowDependencies = {
    hasRuntimeClient: () => hasRuntime,
    showQuitDialog: (count) => {
      calls.dialogShown++;
      return new Promise((resolve, reject) => {
        dialogResolver = resolve;
        dialogRejecter = reject;
        if (options.dialogReject) {
          reject(new Error("Dialog closed unexpectedly"));
        } else if (options.dialogChoice) {
          resolve(options.dialogChoice);
        }
      });
    },
    cancelRun: async (id) => {
      calls.canceledRunIds.push(id);
    },
    shutdownRuntime: async () => {
      calls.shutdownCalls++;
    },
    requestFinalQuit: () => {
      calls.finalQuitCalls++;
    },
    log: (level, msg, meta) => {
      calls.logs.push({ level, msg, meta });
    },
  };

  const controller = new QuitFlowController(projection, deps);
  const event = {
    preventDefault: () => {
      calls.preventDefault++;
    },
  };

  return {
    controller,
    event,
    calls,
    resolveDialog: (choice: "return_to_eidos" | "stop_and_exit") => dialogResolver?.(choice),
    rejectDialog: (err: Error) => dialogRejecter?.(err),
    setRunIds: (ids: string[]) => {
      runIds = ids;
    },
    setHasRuntime: (val: boolean) => {
      hasRuntime = val;
    },
  };
}

test("no Runtime Client permits quit without preventing default or shutting down", () => {
  const h = createHarness({ hasRuntime: false });
  h.controller.handleBeforeQuit(h.event);

  assert.equal(h.calls.preventDefault, 0);
  assert.equal(h.calls.shutdownCalls, 0);
  assert.equal(h.calls.finalQuitCalls, 0);
});

test("no active runs starts shutdown once and calls final quit once", async () => {
  const h = createHarness({ activeRunIds: [] });
  h.controller.handleBeforeQuit(h.event);

  assert.equal(h.calls.preventDefault, 1);
  assert.equal(h.calls.dialogShown, 0);

  // Wait microtask for async shutdown
  await new Promise((r) => setTimeout(r, 0));

  assert.equal(h.calls.shutdownCalls, 1);
  assert.equal(h.calls.finalQuitCalls, 1);

  // Second beforeQuit during shutdown does nothing
  h.controller.handleBeforeQuit(h.event);
  assert.equal(h.calls.shutdownCalls, 1);
});

test("active runs shows dialog once and repeated quit events do not stack dialogs", () => {
  const h = createHarness({ activeRunIds: ["run-1", "run-2"] });
  h.controller.handleBeforeQuit(h.event);

  assert.equal(h.calls.preventDefault, 1);
  assert.equal(h.calls.dialogShown, 1);

  // Repeated quit during dialog does not open second dialog
  h.controller.handleBeforeQuit(h.event);
  assert.equal(h.calls.preventDefault, 2);
  assert.equal(h.calls.dialogShown, 1);
});

test("Return to Eidos resets quit state, sends no cancellation and no shutdown", async () => {
  const h = createHarness({ activeRunIds: ["run-1"] });
  h.controller.handleBeforeQuit(h.event);
  assert.equal(h.controller.getState().isQuitting, true);

  h.resolveDialog("return_to_eidos");
  await new Promise((r) => setTimeout(r, 0));

  assert.equal(h.controller.getState().isQuitting, false);
  assert.equal(h.calls.canceledRunIds.length, 0);
  assert.equal(h.calls.shutdownCalls, 0);
  assert.equal(h.calls.finalQuitCalls, 0);
});

test("Stop Tasks cancels all active run IDs, shuts down runtime and requests final quit once", async () => {
  const h = createHarness({ activeRunIds: ["run-1", "run-2"] });
  h.controller.handleBeforeQuit(h.event);

  h.resolveDialog("stop_and_exit");
  await new Promise((r) => setTimeout(r, 0));

  assert.deepEqual(h.calls.canceledRunIds, ["run-1", "run-2"]);
  assert.equal(h.calls.shutdownCalls, 1);
  assert.equal(h.calls.finalQuitCalls, 1);
});

test("cancellation failure does not block other cancellations or shutdown", async () => {
  const canceled: string[] = [];
  const projection: ActiveRunProjection = {
    runIds: () => ["failing-run", "ok-run"],
    count: () => 2,
  };
  let shutdownDone = false;
  let finalQuitDone = false;

  const deps: QuitFlowDependencies = {
    hasRuntimeClient: () => true,
    showQuitDialog: async () => "stop_and_exit",
    cancelRun: async (id) => {
      canceled.push(id);
      if (id === "failing-run") {
        throw new Error("Cancel network error");
      }
    },
    shutdownRuntime: async () => {
      shutdownDone = true;
    },
    requestFinalQuit: () => {
      finalQuitDone = true;
    },
    log: () => {},
  };

  const controller = new QuitFlowController(projection, deps);
  const event = { preventDefault: () => {} };

  controller.handleBeforeQuit(event);
  await new Promise((r) => setTimeout(r, 10));

  assert.deepEqual(canceled, ["failing-run", "ok-run"]);
  assert.equal(shutdownDone, true);
  assert.equal(finalQuitDone, true);
});

test("dialog rejection safely cancels quit", async () => {
  const h = createHarness({ activeRunIds: ["run-1"], dialogReject: true });
  h.controller.handleBeforeQuit(h.event);

  await new Promise((r) => setTimeout(r, 0));

  assert.equal(h.controller.getState().isQuitting, false);
  assert.equal(h.calls.shutdownCalls, 0);
  assert.equal(h.calls.finalQuitCalls, 0);
});
