import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import {
  buildRuntimeEnvironment,
  RuntimeClient,
  RuntimeRequestError,
} from "./runtime-client.js";
import type { RuntimeNotification } from "./runtime-client.js";


const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const protocolV1Fixture = path.join(projectRoot, "protocol", "fixtures", "v1.json");
const toolResultsV1Fixture = path.join(projectRoot, "protocol", "fixtures", "tool-results-v1.json");
const extensionsV1Fixture = path.join(projectRoot, "protocol", "fixtures", "extensions-v1.json");
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

test("shares closed extension vectors with the Python runtime", async () => {
  const fixture = JSON.parse(await readFile(extensionsV1Fixture, "utf8")) as Record<string, unknown>;
  assert.equal(fixture.extensionContractVersion, 1);
  assert.equal((fixture.plugin as { id: string }).id, "demo");
  assert.equal((fixture.mcpServer as { permissionProfile: string }).permissionProfile, "connector");
  assert.deepEqual(
    (fixture.stepToolSnapshot as { deferredNames: string[] }).deferredNames,
    ["mcp__fixture__echo"],
  );
  assert.equal(
    (fixture.stepResolutionReview as { requestHash: string }).requestHash,
    "5".repeat(64),
  );
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


test("development Runtime environment preserves inherited Python settings", () => {
  const environment = buildRuntimeEnvironment({
    runtimeRoot: "/workspace/runtime",
    baseEnvironment: {
      PYTHONPATH: "/developer/python",
      PYTHONHOME: "/developer/python-home",
      PYTHONNOUSERSITE: "0",
      PYTHONDONTWRITEBYTECODE: "0",
      EIDOS_PYTHON: "/developer/python/bin/python",
    },
    overrides: { EIDOS_FAKE_MODEL: "1" },
    policy: "development",
  });

  assert.equal(environment.PYTHONPATH, [
    "/workspace/runtime",
    "/developer/python",
  ].join(path.delimiter));
  assert.equal(environment.PYTHONHOME, "/developer/python-home");
  assert.equal(environment.PYTHONNOUSERSITE, "0");
  assert.equal(environment.PYTHONDONTWRITEBYTECODE, "0");
  assert.equal(environment.EIDOS_PYTHON, "/developer/python/bin/python");
  assert.equal(environment.EIDOS_FAKE_MODEL, "1");
});


test("packaged Runtime environment is isolated to the bundled app root", () => {
  const environment = buildRuntimeEnvironment({
    runtimeRoot: "/Applications/Eidos.app/Contents/Resources/runtime/app",
    baseEnvironment: {
      PYTHONPATH: "/developer/python",
      PYTHONHOME: "/developer/python-home",
      PYTHONNOUSERSITE: "0",
      PYTHONDONTWRITEBYTECODE: "0",
      EIDOS_PYTHON: "/developer/python/bin/python",
    },
    overrides: { EIDOS_FAKE_MODEL: "1" },
    policy: "packaged",
  });

  assert.equal(
    environment.PYTHONPATH,
    "/Applications/Eidos.app/Contents/Resources/runtime/app",
  );
  assert.equal(environment.PYTHONHOME, undefined);
  assert.equal(environment.PYTHONNOUSERSITE, "1");
  assert.equal(environment.PYTHONDONTWRITEBYTECODE, "1");
  assert.equal(environment.EIDOS_PYTHON, undefined);
  assert.equal(environment.EIDOS_FAKE_MODEL, "1");
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
    assert.equal(initialized.runtimeVersion, "0.3.0");
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
      session: created, runs: [], items: [], stepResolutions: [], throughEventId: 1,
    });
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("imports and manages closed Plugin Skill and MCP records", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const pluginRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-plugin-"));
  await mkdir(path.join(pluginRoot, "skills", "review"), { recursive: true });
  await writeFile(
    path.join(pluginRoot, "skills", "review", "SKILL.md"),
    "---\nname: review\ndescription: Review files.\n---\nInspect first.\n",
    "utf8",
  );
  await writeFile(path.join(pluginRoot, "server.py"), "# fixture\n", "utf8");
  await writeFile(path.join(pluginRoot, "plugin.json"), JSON.stringify({
    schemaVersion: 1,
    id: "desktop_fixture",
    name: "Desktop Fixture",
    version: "1.0.0",
    description: "Fixture",
    skills: [{ root: "skills/review" }],
    mcpServers: [{
      id: "fixture",
      executable: "python3",
      argv: ["server.py"],
      envNames: [],
      permissionProfile: "workspace_read",
      startupTimeoutSeconds: 5,
      toolTimeoutSeconds: 10,
      enabled: true,
    }],
  }), "utf8");

  try {
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
    });
    await client.initialize();
    const imported = await client.importPlugin(pluginRoot);
    await client.setPluginEnabled(imported.id, true);
    const skills = await client.listSkills();
    const servers = await client.listMcpServers();
    const enabled = await client.setMcpEnabled(imported.id, "fixture", true);
    const extensionSnapshot = await client.readExtensions();
    const extensionEvents = await client.readExtensionEvents(0);

    assert.equal((await client.listPlugins()).plugins[0]?.id, "desktop_fixture");
    assert.equal(skills.skills[0]?.qualifiedId, "desktop_fixture:review");
    assert.equal(servers.servers[0]?.permissionProfile, "workspace_read");
    assert.equal(enabled.consented, true);
    assert.equal(extensionSnapshot.plugins[0]?.id, "desktop_fixture");
    assert.ok(extensionEvents.items.some((event) => event.eventType === "mcp_server.state_changed"));
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
    await rm(pluginRoot, { recursive: true, force: true });
  }
});

test("lists only configured models and keeps task model history during session mutations", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-data-"));
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-workspace-"));
  await writeFile(path.join(workspaceRoot, "README.md"), "# Keep me\n", "utf8");

  try {
    let completeRun: ((notification: RuntimeNotification) => void) | undefined;
    const runCompleted = new Promise<RuntimeNotification>((resolve) => {
      completeRun = resolve;
    });
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot: path.join(projectRoot, "runtime"),
      dataDirectory,
      environment: { EIDOS_FAKE_MODEL: "1" },
      onNotification: (notification) => {
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const models = await client.listModels();
    const session = await client.createSession(workspaceRoot);
    const started = await client.startRun(
      session.id, "Read README.md", "deepseek-v4-pro",
    );
    await withTimeout(runCompleted, 5_000);
    const listed = await client.listSessions();
    const renamed = await client.renameSession(session.id, "检查项目");
    const deleted = await client.deleteSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.deepEqual(models.models, []);
    assert.equal(models.defaultModelId, undefined);
    assert.equal(started.modelId, "deepseek-v4-pro");
    assert.equal(listed.items[0]?.taskStatus, "completed");
    assert.equal(renamed.title, "检查项目");
    assert.deepEqual(deleted, { deletedSessionId: session.id });
    assert.equal(await readFile(path.join(workspaceRoot, "README.md"), "utf8"), "# Keep me\n");
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
    const started = await client.startRun(session.id, "Read README.md", "deepseek-v4-flash");
    const completed = await withTimeout(runCompleted, 5_000);
    const snapshot = await client.readSession(session.id);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(started.status, "running");
    assert.equal(completed.method, "run/completed");
    assert.equal(
      notifications.filter(
        (notification) => notification.method === "session/titleUpdated",
      ).length,
      1,
    );
    assert.deepEqual(
      notifications
        .filter(
          (notification) => notification.method !== "session/titleUpdated",
        )
        .map((notification) => notification.method),
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
    assert.equal(snapshot.session.title, "Fixture task");
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
  const approvalNotifications: string[] = [];

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
        if (notification.method.startsWith("approval/")) {
          approvalNotifications.push(notification.method);
        }
        if (notification.method === "run/completed") {
          completeRun?.(notification);
        }
      },
    });

    await client.initialize();
    const session = await client.createSession(workspaceRoot);
    await client.startRun(session.id, "Create approved.txt", "deepseek-v4-flash");
    const completed = await withTimeout(runCompleted, 5_000);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);

    assert.equal(completed.method, "run/completed");
    assert.equal(await readFile(path.join(workspaceRoot, "approved.txt"), "utf8"), "approved\n");
    assert.equal(approvals.length, 1);
    assert.deepEqual(
      approvalNotifications,
      ["approval/requested", "approval/resolved"],
    );
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
    const run = await client.startRun(session.id, "Create approved.txt", "deepseek-v4-flash");
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

test("degraded Shell capability rejects execution without approval or workspace mutation", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-shell-unavailable-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  const workspaceRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-shell-unavailable-workspace-"));
  const sentinel = path.join(workspaceRoot, "sentinel.txt");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(sentinel, "unchanged\n", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, sys",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':1,'runtimeVersion':'fixture','capabilities':{'runShell':False,'modelConfigured':False}}}), flush=True)",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'error':{'code':-32000,'message':'Request failed','data':{'code':'SANDBOX_UNAVAILABLE','retryable':False}}}), flush=True)",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':None}), flush=True)",
    ].join("\n"),
    "utf8",
  );

  try {
    const approvals: unknown[] = [];
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot,
      onApprovalRequest: async (request) => {
        approvals.push(request);
        return { decision: "approve" };
      },
    });
    const initialized = await client.initialize();
    assert.equal(initialized.capabilities.runShell, false);
    await assert.rejects(
      client.startRun("session-1", "Run printf", "deepseek-v4-flash"),
      (error: unknown) => (
        error instanceof RuntimeRequestError
        && error.businessCode === "SANDBOX_UNAVAILABLE"
      ),
    );
    assert.deepEqual(approvals, []);
    assert.equal(await readFile(sentinel, "utf8"), "unchanged\n");
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
    await rm(workspaceRoot, { recursive: true, force: true });
  }
});

test("routes shell approval and streams sandboxed command completion", async (context) => {
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
    if (initialized.capabilities.runShell === false) {
      context.skip("real Seatbelt Self-Test is unavailable in this environment");
      await client.shutdown();
      assert.equal(await client.waitForExit(), 0);
      return;
    }
    const session = await client.createSession(workspaceRoot);
    await client.startRun(session.id, "Run printf", "deepseek-v4-flash");
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

test("projects Approval requests strictly, strips unknown fields, and respects message.id authority", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-approval-proj-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");

  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, sys",
      "def send(message): print(json.dumps(message, separators=(',', ':')), flush=True)",
      "json.loads(sys.stdin.readline())",
      "send({'jsonrpc':'2.0','id':'client-1','result':{'protocolVersion':1,'runtimeVersion':'0.3.0','capabilities':{'runShell':True,'modelConfigured':False}}})",
      "send({'jsonrpc':'2.0','id':'server-file-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i1','toolCallId':'tc1','summary':'file summary','kind':'file_change','diff':'diff text','apiKey':'secret-key','environment':{'PRIVATE_TOKEN':'secret'}}})",
      "sys.stdin.readline()",
      "send({'jsonrpc':'2.0','id':'server-cmd-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i2','toolCallId':'tc2','summary':'cmd summary','kind':'command_execution','command':'ls','cwd':'/tmp','networkEnabled':False,'timeoutSeconds':30,'token':'secret-token'}})",
      "sys.stdin.readline()",
      "send({'jsonrpc':'2.0','id':'server-ext-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i3','toolCallId':'tc3','summary':'ext summary','kind':'external_tool','toolName':'my_tool','arguments':{'foo':'bar'},'provenance':{'kind':'mcp','sourceId':'srv1','sourceVersion':'1.0','contentHash':'h1','extraProvField':'secret'},'permissionProfile':'connector','timeoutSeconds':60,'envNames':['PATH'],'internalDiagnostics':{'stack':'path'}}})",
      "sys.stdin.readline()",
      "send({'jsonrpc':'2.0','id':'server-net-1','method':'item/requestApproval','params':{'id':'forged-id','sessionId':'s1','runId':'r1','itemId':'i4','toolCallId':'tc4','summary':'net summary','kind':'network_access','toolName':'fetch','hosts':['api.example.com'],'target':'https://api.example.com','secretHeader':'Bearer ...'}})",
      "sys.stdin.readline()",
      "json.loads(sys.stdin.readline())",
      "send({'jsonrpc':'2.0','id':'client-2','result':None})",
    ].join("\n"),
    "utf8",
  );

  const receivedRequests: Array<Record<string, unknown>> = [];
  try {
    const client = new RuntimeClient({
      pythonExecutable: pythonExecutable,
      runtimeRoot,
      onApprovalRequest: async (req) => {
        receivedRequests.push(req as unknown as Record<string, unknown>);
        return { decision: "approve" };
      },
    });

    await client.initialize();
    for (let i = 0; i < 50 && receivedRequests.length < 4; i += 1) {
      await new Promise((r) => setTimeout(r, 50));
    }

    assert.equal(receivedRequests.length, 4);

    // 1. file_change
    const req1 = receivedRequests[0]!;
    assert.equal(req1.id, "server-file-1");
    assert.equal(req1.kind, "file_change");
    assert.equal(req1.diff, "diff text");
    assert.equal(req1.apiKey, undefined);
    assert.equal(req1.environment, undefined);
    assert.deepEqual(Object.keys(req1).sort(), [
      "diff", "id", "itemId", "kind", "runId", "sessionId", "summary", "toolCallId",
    ]);

    // 2. command_execution
    const req2 = receivedRequests[1]!;
    assert.equal(req2.id, "server-cmd-1");
    assert.equal(req2.kind, "command_execution");
    assert.equal(req2.command, "ls");
    assert.equal(req2.networkEnabled, false);
    assert.equal(req2.token, undefined);
    assert.deepEqual(Object.keys(req2).sort(), [
      "additionalExecutableAccess", "additionalReadAccess", "additionalWriteAccess",
      "attemptOrdinal", "command", "cwd", "escalationReason", "executionMode",
      "id", "itemId", "kind", "networkEnabled", "reason", "runId",
      "sandboxPermissions", "sessionId", "summary", "timeoutSeconds", "toolCallId",
    ]);

    // 3. external_tool
    const req3 = receivedRequests[2]!;
    assert.equal(req3.id, "server-ext-1");
    assert.equal(req3.kind, "external_tool");
    assert.deepEqual(req3.arguments, { foo: "bar" });
    assert.deepEqual(req3.envNames, ["PATH"]);
    assert.equal(req3.internalDiagnostics, undefined);
    const prov = req3.provenance as Record<string, unknown>;
    assert.equal(prov.extraProvField, undefined);
    assert.deepEqual(Object.keys(req3).sort(), [
      "arguments", "envNames", "id", "itemId", "kind", "permissionProfile", "provenance", "runId", "sessionId", "summary", "timeoutSeconds", "toolCallId", "toolName",
    ]);

    // 4. network_access
    const req4 = receivedRequests[3]!;
    assert.equal(req4.id, "server-net-1");
    assert.equal(req4.kind, "network_access");
    assert.deepEqual(req4.hosts, ["api.example.com"]);
    assert.equal(req4.secretHeader, undefined);
    assert.deepEqual(Object.keys(req4).sort(), [
      "hosts", "id", "itemId", "kind", "runId", "sessionId", "summary", "target", "toolCallId", "toolName",
    ]);

    await client.shutdown();
    await client.waitForExit();
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});

test("rejects ordinary ToolCall unknown provenance at the Runtime Client boundary", async () => {
  const runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-invalid-tool-runtime-"));
  const packageRoot = path.join(runtimeRoot, "eidos_runtime");
  await mkdir(packageRoot);
  await writeFile(path.join(packageRoot, "__init__.py"), "", "utf8");
  await writeFile(
    path.join(packageRoot, "__main__.py"),
    [
      "import json, sys",
      "request = json.loads(sys.stdin.readline())",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'protocolVersion':1,'runtimeVersion':'fixture','capabilities':{'runShell':False,'modelConfigured':False}}}), flush=True)",
      "request = json.loads(sys.stdin.readline())",
      "snapshot = {'session':{'id':'session-1','workspaceRoot':'/tmp','taskStatus':'new','createdAt':0,'updatedAt':0},'runs':[],'items':[{'id':'item-1','sessionId':'session-1','runId':'run-1','ordinal':0,'modelStepIndex':0,'kind':'tool_call','status':'completed','createdAt':0,'completedAt':1,'toolCall':{'id':'tool-1','itemId':'item-1','modelStepIndex':0,'batchOrder':0,'providerCallId':'provider-1','toolName':'mcp__server__tool','status':'completed','startedAt':0,'completedAt':1,'provenance':{'kind':'mcp','sourceId':'server-a','sourceVersion':'1','contentHash':'hash','apiKey':'secret','internalDiagnostics':{'path':'/private/path'}}}}],'throughEventId':1}",
      "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':snapshot}), flush=True)",
      "sys.stdin.read()",
    ].join("\n"),
    "utf8",
  );

  try {
    const notifications: RuntimeNotification[] = [];
    const client = new RuntimeClient({
      pythonExecutable,
      runtimeRoot,
      onNotification: (notification) => notifications.push(notification),
    });
    await client.initialize();

    let snapshot: unknown;
    await assert.rejects(
      client.readSession("session-1").then((value) => { snapshot = value; }),
      /invalid result for session\/read/,
    );
    assert.equal(snapshot, undefined);
    assert.deepEqual(notifications, []);
    assert.notEqual(await client.waitForExit(), 0);
    await assert.rejects(client.health(), /Runtime (client is closed|process is not available)/);
  } finally {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
});
