import { test } from "node:test";
import assert from "node:assert/strict";
import type { ModelListResult } from "../contracts.js";
import { resolveSelectedModel } from "./useModelController.js";

const models: ModelListResult = {
  defaultModelId: "deepseek-v4-pro",
  models: [
    {
      id: "deepseek-v4-pro", name: "DeepSeek-V4 Pro", vendor: "DeepSeek",
      provider: "deepseek", url: "https://api.deepseek.com/chat/completions",
      supportsToolCall: true, supportsImages: false, supportsReasoning: true,
      reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
    },
    {
      id: "MiniMax-M3", name: "MiniMax M3", vendor: "MiniMax",
      provider: "minimax", url: "https://api.minimaxi.com/v1/chat/completions",
      supportsToolCall: true, supportsImages: false, supportsReasoning: true,
      reasoning: { defaultEffort: "high", supportedEfforts: ["high", "max"] },
    },
  ],
};

void test("new sessions select the first configured model", () => {
  assert.equal(resolveSelectedModel(models).selectedModelId, "deepseek-v4-pro");
});

void test("the most recently used session model wins when still configured", () => {
  assert.equal(
    resolveSelectedModel(models, "MiniMax-M3", "deepseek-v4-pro").selectedModelId,
    "MiniMax-M3",
  );
});

void test("deleting the selected model falls back to the first configured model", () => {
  assert.equal(
    resolveSelectedModel(models, undefined, "deleted-model").selectedModelId,
    "deepseek-v4-pro",
  );
});

void test("an empty local configuration has no selection and is not an error", () => {
  assert.deepEqual(
    resolveSelectedModel({ models: [], defaultModelId: null }),
    { selectedModelId: undefined },
  );
});
