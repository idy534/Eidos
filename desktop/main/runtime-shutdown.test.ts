import assert from "node:assert/strict";
import test from "node:test";

import { shutdownRuntime, type RuntimeShutdownClient } from "./runtime-shutdown.js";

test("graceful shutdown clears timeout and never calls terminate()", async () => {
  let shutdownCalled = false;
  let waitForExitCalled = false;
  let terminateCalls = 0;
  const diagnostics: string[] = [];

  const client: RuntimeShutdownClient = {
    async shutdown() {
      shutdownCalled = true;
    },
    async waitForExit() {
      waitForExitCalled = true;
      return 0;
    },
    terminate() {
      terminateCalls++;
    },
  };

  const result = await shutdownRuntime(client, {
    timeoutMs: 1_000,
    onDiagnostic: (_level, msg) => diagnostics.push(msg),
  });

  assert.equal(result.outcome, "graceful");
  assert.equal(result.forced, false);
  assert.equal(shutdownCalled, true);
  assert.equal(waitForExitCalled, true);
  assert.equal(terminateCalls, 0);
  assert.ok(diagnostics.includes("Runtime shutdown complete"));
});

test("timeout calls terminate() once and returns timeout outcome", async () => {
  let terminateCalls = 0;

  const client: RuntimeShutdownClient = {
    async shutdown() {
      // hangs forever
      return new Promise(() => {});
    },
    async waitForExit() {
      return new Promise(() => {});
    },
    terminate() {
      terminateCalls++;
    },
  };

  const result = await shutdownRuntime(client, {
    timeoutMs: 50,
  });

  assert.equal(result.outcome, "timeout");
  assert.equal(result.forced, true);
  assert.equal(terminateCalls, 1);
});

test("shutdown rejection calls terminate() once and returns shutdown_error outcome", async () => {
  let terminateCalls = 0;

  const client: RuntimeShutdownClient = {
    async shutdown() {
      throw new Error("RPC failure");
    },
    async waitForExit() {
      return 0;
    },
    terminate() {
      terminateCalls++;
    },
  };

  const result = await shutdownRuntime(client, {
    timeoutMs: 1_000,
  });

  assert.equal(result.outcome, "shutdown_error");
  assert.equal(result.forced, true);
  assert.equal(terminateCalls, 1);
});

test("exit wait rejection calls terminate() once and returns exit_error outcome", async () => {
  let terminateCalls = 0;

  const client: RuntimeShutdownClient = {
    async shutdown() {
      return undefined;
    },
    async waitForExit() {
      throw new Error("Process crash");
    },
    terminate() {
      terminateCalls++;
    },
  };

  const result = await shutdownRuntime(client, {
    timeoutMs: 1_000,
  });

  assert.equal(result.outcome, "exit_error");
  assert.equal(result.forced, true);
  assert.equal(terminateCalls, 1);
});

test("timeout followed by late graceful completion terminates once and preserves timeout outcome", async () => {
  let terminateCalls = 0;
  let resolveShutdown: (() => void) | undefined;
  let resolveExit: (() => void) | undefined;

  const client: RuntimeShutdownClient = {
    async shutdown() {
      return new Promise<void>((resolve) => {
        resolveShutdown = resolve;
      });
    },
    async waitForExit() {
      return new Promise<number | null>((resolve) => {
        resolveExit = () => resolve(0);
      });
    },
    terminate() {
      terminateCalls++;
    },
  };

  const resultPromise = shutdownRuntime(client, {
    timeoutMs: 20,
  });

  const result = await resultPromise;
  assert.equal(result.outcome, "timeout");
  assert.equal(terminateCalls, 1);

  // Trigger late resolution
  resolveShutdown?.();
  await new Promise((r) => setTimeout(r, 10));
  resolveExit?.();
  await new Promise((r) => setTimeout(r, 10));

  assert.equal(terminateCalls, 1);
});

test("repeated completion paths do not log completion twice", async () => {
  const diagnostics: string[] = [];

  const client: RuntimeShutdownClient = {
    async shutdown() {
      return undefined;
    },
    async waitForExit() {
      return 0;
    },
    terminate() {},
  };

  await shutdownRuntime(client, {
    timeoutMs: 1_000,
    onDiagnostic: (_level, msg) => diagnostics.push(msg),
  });

  const completeLogs = diagnostics.filter((m) => m === "Runtime shutdown complete");
  assert.equal(completeLogs.length, 1);
});

test("a second termination request in terminateOnce is ignored", async () => {
  let terminateCalls = 0;

  const client: RuntimeShutdownClient = {
    async shutdown() {
      throw new Error("Error 1");
    },
    async waitForExit() {
      throw new Error("Error 2");
    },
    terminate() {
      terminateCalls++;
    },
  };

  const result = await shutdownRuntime(client, { timeoutMs: 1_000 });
  assert.equal(result.forced, true);
  assert.equal(terminateCalls, 1);
});

test("custom timeout value is respected", async () => {
  const start = Date.now();
  const client: RuntimeShutdownClient = {
    async shutdown() {
      return new Promise(() => {});
    },
    async waitForExit() {
      return new Promise(() => {});
    },
    terminate() {},
  };

  const result = await shutdownRuntime(client, { timeoutMs: 30 });
  const elapsed = Date.now() - start;

  assert.equal(result.outcome, "timeout");
  assert.ok(elapsed >= 25 && elapsed < 500, `Elapsed time was ${elapsed}ms`);
});
