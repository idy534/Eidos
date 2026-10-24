import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import { isPluginHookReview, isPluginCompatibility, isHookActivityPage, isHookSourceList, isHookConfigurationRequest, isHookHistoryRequest } from "../shared/hooks.js";
import { isPluginPresentation } from "../shared/plugins.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

void test("Hook fixtures match generated Python contracts", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/hooks-v1.json"), "utf8"));
  for (const value of fixture.validReviews) assert.equal(isPluginHookReview(value), true);
  for (const value of fixture.invalidReviews) assert.equal(isPluginHookReview(value), false);
  for (const value of fixture.validCompatibility) assert.equal(isPluginCompatibility(value), true);
  for (const value of fixture.invalidCompatibility) assert.equal(isPluginCompatibility(value), false);
  for (const [name, validate] of [["ActivityPages", isHookActivityPage], ["SourceLists", isHookSourceList], ["ConfigurationRequests", isHookConfigurationRequest], ["HistoryRequests", isHookHistoryRequest]] as const) {
    for (const value of fixture[`valid${name}`]) assert.equal(validate(value), true, JSON.stringify(value));
    for (const value of fixture[`invalid${name}`]) assert.equal(validate(value), false, JSON.stringify(value));
  }
});

void test("Plugin presentation uses the shared closed Python contract", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/extensions-v1.json"), "utf8"));
  const metadata = fixture.pluginWithPresentation.presentation;
  assert.equal(isPluginPresentation(metadata), true);
  assert.equal(isPluginPresentation({ ...metadata, developerName: 42 }), false);
  assert.equal(isPluginPresentation({ ...metadata, accessToken: "not-allowed" }), false);
});

void test("Codex plugins and version-bound Hook trust cross the real Runtime boundary", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-hook-contract-"));
  const source = path.join(temporary, "plugin");
  await mkdir(path.join(source, ".codex-plugin"), { recursive: true });
  await mkdir(path.join(source, "hooks"));
  const manifest = { name: "hook-contract", version: "1.0.0", hooks: "./hooks/hooks.json", interface: { displayName: "Hook Contract", developerName: "Eidos Tests" } };
  await writeFile(path.join(source, ".codex-plugin/plugin.json"), JSON.stringify(manifest));
  await writeFile(path.join(source, "hooks/hooks.json"), JSON.stringify({ hooks: { Stop: [{ hooks: [{ type: "command", command: "printf '{}'" }] }] } }));
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temporary, "data"), environment: { EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    const plugin = await client.importPlugin(source);
    assert.equal(plugin.name, "Hook Contract");
    assert.equal(plugin.presentation?.developerName, "Eidos Tests");
    assert.equal(plugin.compatibility?.format, "codex");
    assert.equal(plugin.hookReview?.trusted ?? false, false);
    assert.equal(plugin.hookReview?.executableCount, 1);
    await client.setPluginEnabled(plugin.id, true);
    const trusted = await client.setPluginHookTrust(plugin.id, plugin.contentHash, true);
    assert.equal(trusted.hookReview?.trusted, true);
    await assert.rejects(client.setPluginHookTrust(plugin.id, "0".repeat(64), true), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "PLUGIN_UNAVAILABLE");
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options);
    await client.initialize();
    assert.equal((await client.listPlugins()).plugins[0]?.hookReview?.trusted, true);
    const revoked = await client.setPluginHookTrust(plugin.id, plugin.contentHash, false);
    assert.equal(revoked.hookReview?.trusted ?? false, false);
    await writeFile(path.join(source, ".codex-plugin/plugin.json"), JSON.stringify({ ...manifest, version: "2.0.0" }));
    const updated = await client.importPlugin(source);
    assert.equal(updated.version, "2.0.0");
    assert.equal(updated.enabled, false);
    assert.equal(updated.hookReview?.trusted ?? false, false);
    assert.equal(updated.hookReview?.executableCount, 1);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temporary, { recursive: true, force: true });
  }
});

void test("Local Hook configuration, content trust, and history cross the Runtime boundary", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-hook-sources-"));
  const data = path.join(temporary, "data");
  const workspace = path.join(temporary, "workspace");
  await mkdir(data, { mode: 0o700 });
  await mkdir(path.join(workspace, ".eidos"), { recursive: true });
  const configuration = { hooks: { SessionStart: [{ hooks: [{ type: "command", command: "printf context" }] }] } };
  await writeFile(path.join(data, "hooks.json"), JSON.stringify(configuration));
  await writeFile(path.join(workspace, ".eidos/config.toml"), "[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = 'command'\ncommand = 'true'\n");
  const client = new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"),
    dataDirectory: data, environment: { EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } });
  try {
    await client.initialize();
    const session = await client.createSession(workspace);
    const sources = (await client.hookConfiguration({ action: "read", sessionId: session.id })).sources ?? [];
    assert.deepEqual(sources.map(source => source.scope), ["user", "project"]);
    const user = sources[0]!;
    assert.equal(user.trusted, false);
    assert.equal(user.definitions?.[0]?.event, "SessionStart");
    const trusted = await client.hookConfiguration({ action: "trust", sourceId: user.id, contentHash: user.contentHash, trusted: true });
    assert.equal(trusted.sources?.[0]?.trusted, true);
    const disabled = await client.hookConfiguration({ action: "enable", sourceId: user.id, contentHash: user.contentHash, enabled: false });
    assert.equal(disabled.sources?.[0]?.enabled, false);
    await writeFile(path.join(data, "hooks.json"), JSON.stringify({ ...configuration, description: "changed" }));
    assert.equal((await client.hookConfiguration({ action: "read" })).sources?.[0]?.trusted, false);
    await assert.rejects(client.hookConfiguration({ action: "trust", sourceId: user.id, contentHash: user.contentHash, trusted: true }),
      (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "HOOK_CONFIGURATION_CHANGED");
    assert.deepEqual((await client.readHookHistory({ sourceId: user.id })).items ?? [], []);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await rm(temporary, { recursive: true, force: true });
  }
});
