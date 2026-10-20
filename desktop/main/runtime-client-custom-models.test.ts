import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RuntimeClient } from "./runtime-client.js";
import type { CustomProviderRequest } from "../shared/custom-models.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
test("custom provider discovery and configuration roundtrip through the real Runtime process", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-custom-models-"));
  const received: { path: string; method: string; authorization: string | undefined }[] = [];
  let listStatus = 200;
  const http = createServer((request, response) => {
    received.push({ path: request.url ?? "", method: request.method ?? "", authorization: request.headers.authorization });
    response.writeHead(listStatus, { "Content-Type": "application/json" });
    response.end(JSON.stringify(listStatus === 200 ? { data: [{ id: "same-model", display_name: "公司模型", context_window: 64000, max_output_tokens: 4096,
      input_modalities: { invalid: true }, modalities: { input: ["text", "image"], output: ["text"] }, reasoning: true, supported_reasoning_levels: [{ effort: "low" }, { effort: "high" }, { effort: "future-level" }, { effort: "xhigh" }] }] } : {}));
  });
  await new Promise<void>(resolve => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  assert.ok(address && typeof address === "object");
  const baseUrl = `http://127.0.0.1:${address.port}/v1`;
  const diagnostics: string[] = [];
  const createClient = () => new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory, onStderr: line => diagnostics.push(line) });
  let client = createClient();
  try {
    await client.initialize();
    const discovered = await client.discoverCustomModels({ provider: "custom", baseUrl, apiKey: "" });
    assert.equal(discovered.state, "success");
    assert.equal(discovered.models?.[0]?.contextWindowTokens, 64000);
    assert.equal(discovered.models?.[0]?.name, "公司模型");
    assert.equal(discovered.models?.[0]?.supportsImages, true);
    assert.deepEqual(discovered.models?.[0]?.reasoningSelections, ["low", "high", "xhigh"]);
    assert.deepEqual(received[0], { path: "/v1/models", method: "GET", authorization: undefined });
    const custom = { providerId: "company-gateway", providerName: "公司接口", baseUrl, name: "模型甲", contextWindowTokens: 64000, maxOutputTokens: 4096, supportsImages: true };
    const automaticReasoning = { defaultSelection: null, selections: discovered.models![0]!.reasoningSelections! };
    const first = await client.createModel({ provider: "custom", modelId: "same-model", apiKey: "", custom: { ...custom, reasoningControl: "configured", reasoning: automaticReasoning } });
    const second = await client.createModel({ provider: "custom", modelId: "same-model", apiKey: "explicit-fixture-key", custom: { ...custom, name: "模型乙" } });
    assert.notEqual(first.id, second.id);
    assert.equal(first.provider, "custom");
    assert.equal(first.modelId, "same-model");
    assert.equal(first.contextWindowTokens, 64000);
    assert.equal(first.custom?.providerId, "company-gateway");
    assert.equal(first.supportsImages, true);
    const wireReasoning = { selections: automaticReasoning.selections };
    assert.deepEqual(first.reasoning, wireReasoning);
    assert.equal("apiKey" in first, false);
    const edited = await client.updateModel({ provider: "custom", id: second.id, modelId: "same-model", custom: { ...custom, name: "模型乙已编辑" } });
    assert.equal(edited.id, second.id);
    assert.equal(edited.custom?.supportsImages, true);
    await client.discoverCustomModels({ provider: "custom", baseUrl, configId: second.id });
    assert.equal(received.at(-1)?.authorization, "Bearer explicit-fixture-key");
    listStatus = 404;
    assert.equal((await client.discoverCustomModels({ provider: "custom", baseUrl, apiKey: "" })).state, "unavailable");
    await assert.rejects(client.discoverCustomModels({ provider: "deepseek", baseUrl } as unknown as CustomProviderRequest));
    assert.ok(received.every(request => request.method === "GET" && request.path === "/v1/models"));
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    client = createClient();
    await client.initialize();
    const models = await client.listModels();
    assert.deepEqual(models.models.map(model => model.id), [first.id, second.id]);
    assert.deepEqual(models.models[0]?.custom?.reasoning, wireReasoning);
    assert.deepEqual(models.models[0]?.reasoning, wireReasoning);
    assert.equal(models.models[0]?.supportsImages, true);
    assert.equal(models.models[1]?.custom?.supportsImages, true);
    assert.equal(models.models[1]?.name, "模型乙已编辑");
    assert.equal(models.models[1]?.custom?.providerId, "company-gateway");
    assert.equal(diagnostics.join("\n").includes("explicit-fixture-key"), false);
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    await new Promise<void>(resolve => http.close(() => resolve()));
    await rm(dataDirectory, { recursive: true, force: true });
  }
});

test("pending custom discovery leaves health responsive and shuts down with its request", async () => {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-custom-model-cancel-"));
  let markStarted!: () => void;
  const started = new Promise<void>(resolve => { markStarted = resolve; });
  let closed = false;
  const http = createServer((_request, response) => {
    response.on("close", () => { closed = true; });
    markStarted();
  });
  await new Promise<void>(resolve => http.listen(0, "127.0.0.1", resolve));
  const address = http.address();
  assert.ok(address && typeof address === "object");
  const client = new RuntimeClient({ pythonExecutable: path.join(root, ".venv/bin/python"), runtimeRoot: path.join(root, "runtime"), dataDirectory });
  try {
    await client.initialize();
    const discovery = client.discoverCustomModels({ provider: "custom", baseUrl: `http://127.0.0.1:${address.port}`, apiKey: "" }).catch(() => null);
    await Promise.race([started, new Promise<never>((_, reject) => setTimeout(() => reject(new Error("discovery did not start")), 5000).unref())]);
    assert.deepEqual(await client.health(), { state: "ready" });
    await client.shutdown();
    assert.equal(await client.waitForExit(), 0);
    const result = await discovery;
    assert.ok(result === null || result.state === "canceled");
    assert.equal(closed, true);
  } finally {
    client.terminate();
    await client.waitForExit().catch(() => undefined);
    http.closeAllConnections();
    await new Promise<void>(resolve => http.close(() => resolve()));
    await rm(dataDirectory, { recursive: true, force: true });
  }
});
