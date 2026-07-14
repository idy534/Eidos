import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { RuntimeClient } from "./runtime-client.js";
import type { RuntimeNotification } from "./runtime-client.js";


const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");


test("spawns the Python runtime and completes initialize then shutdown", async () => {
  const stderrLines: string[] = [];
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));

  try {
    const client = new RuntimeClient({
      pythonExecutable: process.env.EIDOS_PYTHON ?? "python3",
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      onStderr: (line) => stderrLines.push(line),
    });

    const initialized = await client.initialize();
    assert.deepEqual(initialized, {
      protocolVersion: 1,
      runtimeVersion: "0.1.0",
      capabilities: { runShell: false, modelConfigured: false },
    });

    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    assert.ok(stderrLines.some((line) => line.includes("Runtime initialized")));
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
  }
});

test("creates and reads a persisted session across runtime restarts", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-workspace-"));

  try {
    const firstClient = new RuntimeClient({
      pythonExecutable: process.env.EIDOS_PYTHON ?? "python3",
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await firstClient.initialize();
    const created = await firstClient.createSession(workspaceRoot);
    await firstClient.shutdown();
    assert.equal(await firstClient.waitForExit(), 0);

    const secondClient = new RuntimeClient({
      pythonExecutable: process.env.EIDOS_PYTHON ?? "python3",
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await secondClient.initialize();
    const listed = await secondClient.listSessions();
    const snapshot = await secondClient.readSession(created.id);
    await secondClient.shutdown();
    assert.equal(await secondClient.waitForExit(), 0);

    assert.deepEqual(listed, { items: [created] });
    assert.deepEqual(snapshot, { session: created, runs: [], items: [] });
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("routes runtime notifications during a fake model read loop", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-workspace-"));
  await writeFile(path.join(workspaceRoot, "README.md"), "# Fixture\n", "utf8");
  const notifications: RuntimeNotification[] = [];

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: process.env.EIDOS_PYTHON ?? "python3",
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "1" },
      onNotification: (notification) => {
        notifications.push(notification);
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const session = await client.createSession(workspaceRoot);
    const started = await client.startRun(session.id, "Read README.md");
    const completed = await withTimeout(runCompleted, 5_000);
    const snapshot = await client.readSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(started.status, "running");
    assert.equal(completed.method, "run/completed");
    assert.deepEqual(
      notifications.map((notification) => notification.method),
      [
        "run/started",
        "item/started",
        "item/completed",
        "item/started",
        "item/completed",
        "item/started",
        "item/delta",
        "item/completed",
        "run/completed",
      ],
    );
    assert.equal(snapshot.runs[0]?.status, "succeeded");
    assert.deepEqual(
      snapshot.items.map((item) => item.kind),
      ["user_message", "tool_call", "assistant_message"],
    );
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error("run completion timed out")), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

test("terminates a runtime that writes non-protocol stdout", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-invalid-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import sys",
      "sys.stdin.readline()",
      "print('not-json', flush=True)",
      "sys.stdin.read()",
    ].join("\n"),
    "utf8",
  );

  try {
    const client = new RuntimeClient({
      pythonExecutable: process.env.EIDOS_PYTHON ?? "python3",
      runtimeRoot,
    });

    await assert.rejects(
      client.initialize(),
      /Runtime wrote invalid JSON to stdout/,
    );
    await client.waitForExit();
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});
