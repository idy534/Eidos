import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import type { RuntimeNotification } from "./runtime-client.js";


const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const protocolV1Fixture = path.join(projectRoot, "protocol", "fixtures", "v1.json");
const toolResultsV1Fixture = path.join(projectRoot, "protocol", "fixtures", "tool-results-v1.json");
const pythonExecutable = process.env.EIDOS_PYTHON
  ?? path.join(projectRoot, ".venv", "bin", "python");


test("shares canonical ToolResult vectors with the Python runtime", async () => {
  const fixture = JSON.parse(await readFile(toolResultsV1Fixture, "utf8")) as {
    toolContractVersion: number;
    vectors: Array<{ result: unknown; canonicalJson: string }>;
  };
  assert.equal(fixture.toolContractVersion, 1);
  for (const vector of fixture.vectors) {
    assert.equal(JSON.stringify(sortJson(vector.result)), vector.canonicalJson);
  }
});


test("preserves a closed business error code without exposing runtime details", () => {
  const error = new RuntimeRequestError({
    code: -32000,
    message: "Request failed",
    data: { code: "RUN_ALREADY_ACTIVE", retryable: false },
  });

  assert.equal(error.message, "EIDOS_RUNTIME_ERROR:RUN_ALREADY_ACTIVE");
  assert.equal(error.businessCode, "RUN_ALREADY_ACTIVE");
});


test("spawns the Python runtime and completes initialize then shutdown", async () => {
  const stderrLines: string[] = [];
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));

  try {
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      onStderr: (line) => stderrLines.push(line),
    });

    const initialized = await client.initialize();
    assert.equal(initialized.protocolVersion, 1);
    assert.equal(initialized.runtimeVersion, "0.2.0");
    assert.equal(typeof initialized.capabilities.runShell, "boolean");
    assert.equal(initialized.capabilities.modelConfigured, false);
    assert.deepEqual(await client.health(), { state: "ready" });

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
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await firstClient.initialize();
    const created = await firstClient.createSession(workspaceRoot);
    await firstClient.shutdown();
    assert.equal(await firstClient.waitForExit(), 0);

    const secondClient = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await secondClient.initialize();
    const listed = await secondClient.listSessions();
    const snapshot = await secondClient.readSession(created.id);
    await secondClient.shutdown();
    assert.equal(await secondClient.waitForExit(), 0);

    assert.deepEqual(listed, { items: [created] });
    assert.deepEqual(snapshot, {
      session: created, runs: [], items: [], throughEventId: 1,
    });
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
      pythonExecutable: pythonExecutable,
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
        "run/updated",
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

test("routes a runtime approval request and commits only after approval", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-workspace-"));
  const approvals: string[] = [];

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "write" },
      onApprovalRequest: async (request) => {
        assert.equal(request.kind, "file_change");
        if (request.kind === "file_change") {
          approvals.push(request.diff);
        }
        return { decision: "approve" };
      },
      onNotification: (notification) => {
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const session = await client.createSession(workspaceRoot);
    await client.startRun(session.id, "Create approved.txt");
    const completed = await withTimeout(runCompleted, 5_000);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(completed.method, "run/completed");
    assert.equal(await readFile(path.join(workspaceRoot, "approved.txt"), "utf8"), "approved\n");
    assert.equal(approvals.length, 1);
    assert.match(approvals[0] ?? "", /\+\+\+ b\/approved\.txt/);
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("cancel while awaiting approval ignores a late approve response", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-workspace-"));

  try {
    let resolveApproval: ((decision: { decision: "approve" }) => void) | undefined;
    let approvalStarted: (() => void) | undefined;
    const approvalRequested = new Promise<void>((resolve) => {
      approvalStarted = resolve;
    });
    const delayedApproval = new Promise<{ decision: "approve" }>((resolve) => {
      resolveApproval = resolve;
    });
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "write" },
      onApprovalRequest: async () => {
        approvalStarted?.();
        return delayedApproval;
      },
      onNotification: (notification) => {
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const session = await client.createSession(workspaceRoot);
    const run = await client.startRun(session.id, "Create approved.txt");
    await withTimeout(approvalRequested, 5_000);
    await client.cancelRun(run.id);
    const completed = await withTimeout(runCompleted, 5_000);
    resolveApproval?.({ decision: "approve" });
    await new Promise<void>((resolve) => setImmediate(resolve));
    const snapshot = await client.readSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(completed.method, "run/completed");
    assert.equal(completed.params.run.status, "canceled");
    assert.equal(snapshot.runs[0]?.status, "canceled");
    await assert.rejects(readFile(path.join(workspaceRoot, "approved.txt"), "utf8"));
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("routes shell approval and streams sandboxed command completion", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-workspace-"));
  const approvals: string[] = [];
  let client: RuntimeClient | undefined;

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "shell" },
      onApprovalRequest: async (request) => {
        assert.equal(request.kind, "command_execution");
        if (request.kind === "command_execution") {
          approvals.push(request.command);
          assert.equal(request.networkEnabled, false);
        }
        return { decision: "approve" };
      },
      onNotification: (notification) => {
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    const initialized = await client.initialize();
    assert.equal(initialized.capabilities.runShell, true);
    const session = await client.createSession(workspaceRoot);
    await client.startRun(session.id, "Run printf");
    await withTimeout(runCompleted, 5_000);
    const snapshot = await client.readSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.deepEqual(approvals, ["printf desktop-shell-ok"]);
    const commandItem = snapshot.items.find((item) => item.kind === "command_execution");
    assert.ok(commandItem?.toolCall?.resultJson?.includes("desktop-shell-ok"));
  } finally {
    client?.terminate();
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

function sortJson(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, sortJson(item)]),
    );
  }
  return value;
}

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
      pythonExecutable: pythonExecutable,
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

test("uses the shared v1 vectors for requests, approvals, and notifications", async () => {
  const vectors = JSON.parse(await readFile(protocolV1Fixture, "utf8")) as {
    initialize: { request: object; response: object };
    shutdown: { request: object; response: object };
    approval: { request: object; approveResponse: object };
    notifications: object[];
  };
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-vector-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, pathlib, sys",
      `vectors = json.loads(pathlib.Path(${JSON.stringify(protocolV1Fixture)}).read_text(encoding='utf-8'))`,
      "def receive(expected):",
      "    actual = json.loads(sys.stdin.readline())",
      "    if actual != expected: raise SystemExit(2)",
      "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
      "receive(vectors['initialize']['request'])",
      "send(vectors['initialize']['response'])",
      "send(vectors['approval']['request'])",
      "receive(vectors['approval']['approveResponse'])",
      "for notification in vectors['notifications']: send(notification)",
      "receive(vectors['shutdown']['request'])",
      "send(vectors['shutdown']['response'])",
    ].join("\n"),
    "utf8",
  );

  try {
    const notifications: RuntimeNotification[] = [];
    let approvalSeen: (() => void) | undefined;
    const approvalReceived = new Promise<void>((resolve) => {
      approvalSeen = resolve;
    });
    let runFinished: (() => void) | undefined;
    const runCompleted = new Promise<void>((resolve) => {
      runFinished = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
      onApprovalRequest: async () => {
        approvalSeen?.();
        return { decision: "approve" };
      },
      onNotification: (notification) => {
        notifications.push(notification);
        if (notification.method === "run/completed") {
          runFinished?.();
        }
      },
    });

    assert.equal((await client.initialize()).protocolVersion, 1);
    await withTimeout(approvalReceived, 5_000);
    await withTimeout(runCompleted, 5_000);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    assert.deepEqual(
      notifications.map((notification) => notification.method),
      ["run/started", "item/started", "item/delta", "item/completed", "run/completed"],
    );
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("rejects an oversized unterminated frame before waiting for a newline", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-oversize-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import sys, time",
      "sys.stdin.readline()",
      "for _ in range(17):",
      "    sys.stdout.buffer.write(b'x' * (64 * 1024))",
      "    sys.stdout.buffer.flush()",
      "    time.sleep(0.005)",
      "time.sleep(5)",
    ].join("\n"),
    "utf8",
  );

  try {
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
    });
    await assert.rejects(
      withTimeout(client.initialize(), 3_000),
      /Runtime response exceeds 1 MiB/,
    );
    await client.waitForExit();
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("drains a bounded notification burst even when its consumer is slow", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-slow-consumer-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, pathlib, sys",
      `vectors = json.loads(pathlib.Path(${JSON.stringify(protocolV1Fixture)}).read_text(encoding='utf-8'))`,
      "json.loads(sys.stdin.readline())",
      "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
      "send(vectors['initialize']['response'])",
      "send(vectors['notifications'][0])",
      "send(vectors['notifications'][1])",
      "for sequence in range(1, 129):",
      "    message = json.loads(json.dumps(vectors['notifications'][2]))",
      "    message['params']['sequence'] = sequence",
      "    message['params']['delta'] = 'x' * 512",
      "    send(message)",
      "send(vectors['notifications'][3])",
      "send(vectors['notifications'][4])",
      "json.loads(sys.stdin.readline())",
      "send(vectors['shutdown']['response'])",
    ].join("\n"),
    "utf8",
  );

  try {
    let deltaCount = 0;
    let finish: (() => void) | undefined;
    const completed = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
      onNotification: (notification) => {
        const busyUntil = Date.now() + 1;
        while (Date.now() < busyUntil) {
          // Exercise bounded stdout backpressure with a deliberately slow callback.
        }
        if (notification.method === "item/delta") {
          deltaCount += 1;
        } else if (notification.method === "run/completed") {
          finish?.();
        }
      },
    });

    await client.initialize();
    await withTimeout(completed, 5_000);
    assert.equal(deltaCount, 128);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});
