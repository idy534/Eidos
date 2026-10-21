import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient, RuntimeRequestError } from "./runtime-client.js";
import { isWebSearchReadRequest, isWebSearchSaveRequest, isWebSearchResponse } from "../shared/web-search.js";
import { webSearchOfficialUrl } from "./web-search-links.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
void test("web search fixtures match the generated Python DTOs", async () => {
  const fixture = JSON.parse(await readFile(path.join(root, "protocol/fixtures/web-search-v1.json"), "utf8"));
  for (const [name, validate] of [["ReadRequests", isWebSearchReadRequest], ["Requests", isWebSearchSaveRequest], ["Responses", isWebSearchResponse]] as const) {
    for (const value of fixture[`valid${name}`]) assert.equal(validate(value), true);
    for (const value of fixture[`invalid${name}`]) assert.equal(validate(value), false);
  }
});
void test("official pages are a fixed allowlist", () => {
  for (const provider of ["deepseek", "langsearch"] as const) {
    for (const target of ["keys", "docs", "pricing"] as const) assert.match(webSearchOfficialUrl({ provider, target }), /^https:\/\//);
  }
  assert.equal(webSearchOfficialUrl({ provider: "deepseek", target: "keys" }), "https://platform.deepseek.com/api_keys");
  assert.throws(() => webSearchOfficialUrl({ provider: "deepseek", target: "keys", url: "https://evil.invalid" }));
  assert.throws(() => webSearchOfficialUrl({ provider: "duckduckgo", target: "keys" }));
});
void test("settings remain redacted, revision guarded and persist across Runtime restart", { timeout: 30000 }, async () => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "eidos-web-search-"));
  const options = { pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory: path.join(temporary, "data"),
    environment: { EIDOS_PROJECTLESS_ROOT: path.join(temporary, "workspaces") } };
  let client = new RuntimeClient(options);
  try {
    await client.initialize();
    assert.equal((await client.readWebSearch()).config.defaultProvider ?? null, null);
    const request = { expectedRevision: 0, config: { defaultProvider: "langsearch" as const },
      credentialChanges: [{ provider: "langsearch" as const, action: "set" as const, value: "sample-search-secret" }] };
    const saved = await client.saveWebSearch(request);
    assert.equal(saved.revision, 1); assert.equal(saved.credentials.langsearch, "stored");
    assert.equal(JSON.stringify(saved).includes("sample-search-secret"), false);
    assert.equal(Object.hasOwn(saved, "credentialValues"), false);
    await assert.rejects(client.saveWebSearch(request), (error: unknown) => error instanceof RuntimeRequestError && error.businessCode === "WEB_SEARCH_CONFIG_CONFLICT");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
    client = new RuntimeClient(options); await client.initialize();
    const restored = await client.readWebSearch();
    assert.equal(restored.config.defaultProvider, "langsearch"); assert.equal(restored.credentials.langsearch, "stored");
    assert.equal(JSON.stringify(restored).includes("sample-search-secret"), false);
    assert.equal(Object.hasOwn(restored, "credentialValues"), false);
    const cleared = await client.saveWebSearch({ expectedRevision: 1, config: restored.config, credentialChanges: [{ provider: "langsearch", action: "clear" }] });
    assert.equal(cleared.config.defaultProvider ?? null, null); assert.equal(cleared.credentials.langsearch, "missing");
    await client.shutdown(); assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate(); await client.waitForExit().catch(() => undefined); await rm(temporary, { recursive: true, force: true });
  }
});
