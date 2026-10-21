import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import { isShellEnvironmentReadRequest, isShellEnvironmentSaveRequest, isShellEnvironmentResponse } from "../shared/shell-environment.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

void test("shell environment fixtures match the generated contract", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/shell-environment-v1.json"), "utf8"));
  for (const [name, validate] of [["ReadRequests", isShellEnvironmentReadRequest], ["Requests", isShellEnvironmentSaveRequest], ["Responses", isShellEnvironmentResponse]] as const) {
    for (const value of fixture[`valid${name}`]) assert.equal(validate(value), true);
    for (const value of fixture[`invalid${name}`]) assert.equal(validate(value), false);
  }
});

void test("shell environment settings persist, refresh and reject stale saves across Runtime restart", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-shell-environment-"));
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temporary, "data"),
    environment: { EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    assert.equal((await client.readShellEnvironment()).policy.inherit, "all");
    const request = { expectedRevision: 0, policy: { inherit: "core" as const } };
    const saved = await client.saveShellEnvironment(request);
    assert.equal(saved.revision, 1);
    assert.equal(saved.policy.inherit, "core");
    assert.deepEqual(await client.refreshShellEnvironment(), saved);
    await assert.rejects(client.saveShellEnvironment(request), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "SHELL_ENVIRONMENT_CONFIG_CONFLICT");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options); await client.initialize();
    assert.deepEqual(await client.readShellEnvironment(), saved);
    const changed = await client.saveShellEnvironment({ expectedRevision: 1, policy: { inherit: "none" } });
    assert.equal(changed.revision, 2); assert.equal(changed.policy.inherit, "none");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate(); await client.waitForExit().catch(() => undefined);
    await rm(temporary, { recursive: true, force: true });
  }
});
