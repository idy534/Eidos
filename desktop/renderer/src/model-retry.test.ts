import assert from "node:assert/strict";
import test from "node:test";
import type { ModelRetryActivity } from "../../shared/context.generated.js";
import {
  mergeModelRetries,
  modelFailurePresentation,
  modelRetryLabels,
  modelRetryPhase,
  resolveModelRetryTarget,
  totalModelRetries,
} from "./model-retry.js";
import { terminalRunPresentation } from "./run-presentation.js";

const activity = (overrides: Partial<ModelRetryActivity> = {}): ModelRetryActivity => ({
  requestGroupId: "group", runId: "run", eventId: 5, startedAt: 1_000, updatedAt: 1_000,
  status: "waiting", retryIndex: 2, cause: "connection_failed", nextRetryAt: 6_000, ...overrides,
});

test("keeps the newest event for a request group and the earliest start", () => {
  const merged = mergeModelRetries(
    [activity({ eventId: 5, startedAt: 1_000 })],
    [activity({ eventId: 9, startedAt: 1_200, status: "recovered" }), activity({ requestGroupId: "other", eventId: 2, startedAt: 500 })],
  );
  assert.deepEqual(merged.map((entry) => [entry.requestGroupId, entry.status, entry.startedAt]),
    [["other", "waiting", 500], ["group", "recovered", 1_000]]);
  assert.equal(mergeModelRetries([activity({ eventId: 9, status: "recovered" })], [activity({ eventId: 5 })])[0]!.status, "recovered");
});

test("derives the phase from the activity, the clock and the Run", () => {
  assert.equal(modelRetryPhase(activity(), { status: "running" }, 2_000), "waiting");
  assert.equal(modelRetryPhase(activity(), { status: "running" }, 6_000), "requesting");
  assert.equal(modelRetryPhase(activity({ status: "requesting" }), { status: "running" }, 2_000), "requesting");
  assert.equal(modelRetryPhase(activity(), { status: "canceled" }, 2_000), "canceled");
  assert.equal(modelRetryPhase(activity(), { status: "failed" }, 2_000), "failed");
  assert.equal(modelRetryPhase(activity({ status: "recovered" }), { status: "succeeded" }, 2_000), "recovered");
});

test("names each cause and counts only retries that were sent", () => {
  const causes = ["connection_failed", "timeout", "service_unavailable", "rate_limited", "stream_interrupted"] as const;
  const titles = causes.map((cause) => modelRetryLabels(activity({ cause }), "waiting").title);
  assert.equal(new Set(titles).size, causes.length);
  assert.equal(modelRetryLabels(activity({ cause: "service_unavailable", httpStatus: 503 }), "waiting").caption, "服务商返回 503");
  assert.match(modelRetryLabels(activity({ cause: "rate_limited", retryAfterApplied: true }), "waiting").caption!, /Retry-After/);
  assert.equal(modelRetryLabels(activity(), "requesting").title, "正在第 2 次重试…");
  assert.match(modelRetryLabels(activity({ status: "recovered", retryIndex: 3, updatedAt: 22_000 }), "recovered").title, /共重试 3 次，用时 21 秒/);
  assert.equal(totalModelRetries([activity(), activity({ requestGroupId: "b", status: "recovered", retryIndex: 3 })]), 4);
});

test("explains provider failures with an action only when the user can change something", () => {
  const failed = (reason: string, httpStatus?: number) => modelFailurePresentation({
    status: "failed", termination: { stage: "model", reason, ...(httpStatus ? { httpStatus } : {}) },
  });
  assert.equal(failed("authentication_failed", 401)?.title, "模型服务拒绝了 API Key");
  assert.equal(failed("authentication_failed", 401)?.action?.kind, "model_settings");
  assert.equal(failed("insufficient_quota", 402)?.title, "模型账户额度不足");
  assert.equal(failed("content_filter")?.action, undefined);
  assert.equal(failed("repeated_tool_call"), undefined);
  assert.equal(modelFailurePresentation({ status: "stopped", termination: { stage: "model", reason: "provider_unavailable" } }), undefined);
  const exhausted = modelFailurePresentation(
    { status: "failed", termination: { stage: "model", reason: "provider_unavailable", httpStatus: 503 } },
    activity({ status: "failed", retryIndex: 5, cause: "service_unavailable" }),
  )!;
  assert.match(exhausted.title, /多次重试后仍无法完成请求 · 模型服务暂时不可用/);
  assert.match(exhausted.body, /已自动重试 5 次/);
  assert.deepEqual(exhausted.chips, ["provider_unavailable", "HTTP 503"]);
  assert.doesNotMatch(failed("provider_unavailable")!.body, /已自动重试/);
});

test("treats an in-stream provider failure as an interrupted reply, not a rejected request", () => {
  const presentation = modelFailurePresentation({
    status: "failed", termination: { stage: "model", reason: "stream_interrupted" },
  })!;
  assert.match(presentation.title, /回复在接收过程中中断/);
  assert.doesNotMatch(presentation.title + presentation.body, /拒绝了请求参数/);
  assert.equal(terminalRunPresentation({ status: "failed", termination: { stage: "model", reason: "stream_interrupted" } })!.label, "模型回复在接收过程中中断");
});

test("resolves custom provider and model names instead of custom identifiers", () => {
  const customModel = {
    id: "custom-d30de9db-a2c6-472b-8ca3-7349f6c8c57e",
    modelId: "company/reasoner-v1",
    name: "公司推理模型",
    vendor: "公司网关",
    provider: "custom",
    url: "https://gateway.invalid/v1/chat/completions",
    supportsToolCall: true,
    supportsImages: false,
    supportsReasoning: true,
    custom: {
      providerId: "company-gateway",
      providerName: "公司网关",
      baseUrl: "https://gateway.invalid/v1",
      name: "公司推理模型",
      contextWindowTokens: 64000,
      maxOutputTokens: 4096,
    },
  };
  assert.deepEqual(
    resolveModelRetryTarget(
      { providerId: "custom", modelId: customModel.id },
      { modelId: customModel.id, remoteModelId: "company/reasoner-v1" },
      [customModel],
    ),
    { providerId: "公司网关", modelId: "公司推理模型" },
  );
  assert.deepEqual(
    resolveModelRetryTarget(
      { providerId: "custom", modelId: customModel.id },
      { modelId: customModel.id, remoteModelId: "company/reasoner-v1" },
      [],
    ),
    { providerId: undefined, modelId: "company/reasoner-v1" },
  );
  assert.deepEqual(
    resolveModelRetryTarget(
      { providerId: "deepseek", modelId: "deepseek-flash" },
      { modelId: "deepseek-flash" },
      [],
    ),
    { providerId: "deepseek", modelId: "deepseek-flash" },
  );
});

test("does not label a built-in retry with a custom model that shares its remote model ID", () => {
  const customModel = {
    id: "custom-0b7c2f1e-9a4d-4c35-8e21-5f6a7b8c9d0e",
    modelId: "deepseek-flash",
    name: "我的代理",
    vendor: "公司网关",
    provider: "custom",
    url: "https://gateway.invalid/v1/chat/completions",
    supportsToolCall: true,
    supportsImages: false,
    supportsReasoning: false,
    custom: {
      providerId: "company-gateway",
      providerName: "公司网关",
      baseUrl: "https://gateway.invalid/v1",
      name: "我的代理",
      contextWindowTokens: 64000,
      maxOutputTokens: 4096,
    },
  };
  const builtInModel = {
    id: "deepseek-flash",
    name: "DeepSeek-V4.1 Flash",
    vendor: "DeepSeek",
    provider: "deepseek",
    url: "https://api.deepseek.invalid/v1/chat/completions",
    supportsToolCall: true,
    supportsImages: false,
    supportsReasoning: true,
  };
  assert.deepEqual(
    resolveModelRetryTarget(
      { providerId: "deepseek", modelId: "deepseek-flash" },
      { modelId: "deepseek-flash" },
      [customModel, builtInModel],
    ),
    { providerId: "deepseek", modelId: "deepseek-flash" },
  );
});
