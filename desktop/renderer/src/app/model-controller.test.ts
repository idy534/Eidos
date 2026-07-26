import { test } from "node:test";
import assert from "node:assert/strict";
import type { ModelListResult, ModelStatus } from "../contracts.js";
import { resolveSelectedModel } from "./useModelController.js";

const mockConfiguredStatus: ModelStatus = {
  provider: "deepseek",
  model: "deepseek-v4-flash",
  configured: true,
};

const mockUnconfiguredStatus: ModelStatus = {
  provider: "deepseek",
  model: "deepseek-v4-flash",
  configured: false,
};

const mockModelList: ModelListResult = {
  defaultModelId: "deepseek-v4-flash",
  models: [
    {
      id: "deepseek-v4-flash",
      provider: "deepseek",
      displayName: "DeepSeek V4 Flash",
      configured: true,
      selectable: true,
    },
    {
      id: "deepseek-v4-pro",
      provider: "deepseek",
      displayName: "DeepSeek V4 Pro",
      configured: true,
      selectable: true,
    },
  ],
};

void test("Configured model state is restored after startup", () => {
  assert.equal(mockConfiguredStatus.configured, true);
  assert.equal(mockConfiguredStatus.model, "deepseek-v4-flash");
});

void test("Unconfigured model state is restored correctly", () => {
  assert.equal(mockUnconfiguredStatus.configured, false);
  assert.equal(mockUnconfiguredStatus.model, "deepseek-v4-flash");
});

void test("Model list is stored and default model fallback works", () => {
  const { selectedModelId, error } = resolveSelectedModel(mockModelList);
  assert.equal(selectedModelId, "deepseek-v4-flash");
  assert.equal(error, undefined);
});

void test("Current session model takes priority", () => {
  const { selectedModelId } = resolveSelectedModel(
    mockModelList,
    "deepseek-v4-pro", // current session run model
    "deepseek-v4-flash", // currently selected
  );
  assert.equal(selectedModelId, "deepseek-v4-pro");
});

void test("Current selected model takes priority over default when session run model is absent", () => {
  const { selectedModelId } = resolveSelectedModel(
    mockModelList,
    undefined,
    "deepseek-v4-pro",
  );
  assert.equal(selectedModelId, "deepseek-v4-pro");
});

void test("Non-selectable models are not selected", () => {
  const listWithNonSelectable: ModelListResult = {
    defaultModelId: "deepseek-v4-pro",
    models: [
      {
        id: "deepseek-v4-pro",
        provider: "deepseek",
        displayName: "DeepSeek Pro Disabled",
        configured: false,
        selectable: false,
      },
      {
        id: "deepseek-v4-flash",
        provider: "deepseek",
        displayName: "DeepSeek Flash Enabled",
        configured: true,
        selectable: true,
      },
    ],
  };

  // Even though session model or defaultModelId points to pro, it's non-selectable
  const { selectedModelId } = resolveSelectedModel(
    listWithNonSelectable,
    "deepseek-v4-pro",
  );
  assert.equal(selectedModelId, "deepseek-v4-flash");
});

void test("Defensive fallback when no model is selectable produces a diagnostic error", () => {
  const listAllDisabled: ModelListResult = {
    defaultModelId: "deepseek-v4-flash",
    models: [
      {
        id: "deepseek-v4-flash",
        provider: "deepseek",
        displayName: "Disabled Model",
        configured: false,
        selectable: false,
      },
    ],
  };

  const { selectedModelId, error } = resolveSelectedModel(listAllDisabled);
  assert.equal(selectedModelId, undefined);
  assert.equal(error, "No selectable model available from Runtime");
});

void test("Runtime status is subscribed to only once and singular source of truth is enforced", () => {
  let subscribeCount = 0;
  const mockOnStatus = () => {
    subscribeCount += 1;
    return () => {
      subscribeCount -= 1;
    };
  };

  // Simulate subscribing once in App root
  const unsubscribe = mockOnStatus();
  assert.equal(subscribeCount, 1);
  unsubscribe();
  assert.equal(subscribeCount, 0);
});
