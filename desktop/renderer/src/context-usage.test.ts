import assert from "node:assert/strict";
import test from "node:test";

import { formatContextUsage, formatTokenCount, type ContextUsage } from "./context-usage.js";

const providerUsage: ContextUsage = {
  activeTokens: 185_000,
  windowTokens: 258_000,
  percentUsed: 71.7,
  source: "provider",
};

test("formats provider usage as the active context, not cumulative consumption", () => {
  assert.equal(formatContextUsage(providerUsage), "上下文 72% · 185K / 258K");
});

test("marks estimated values so the UI does not present a fallback as fact", () => {
  assert.equal(formatContextUsage({
      ...providerUsage,
      percentUsed: 23,
      windowTokens: 803_000,
      source: "estimated",
  }), "上下文 ≈23% · ≈185K / 803K");
});

test("renders the no-data state without inventing a zero usage", () => {
  assert.equal(formatContextUsage(undefined), "上下文 --");
});

test("keeps zero and full-window values readable", () => {
  assert.equal(
    formatContextUsage({ ...providerUsage, activeTokens: 0, percentUsed: 0 }),
    "上下文 0% · 0 / 258K",
  );
  assert.equal(
    formatContextUsage({ ...providerUsage, activeTokens: 258_000, percentUsed: 100 }),
    "上下文 100% · 258K / 258K",
  );
});

test("uses compact K/M units without losing small values", () => {
  assert.equal(formatTokenCount(999), "999");
  assert.equal(formatTokenCount(1_500), "1.5K");
  assert.equal(formatTokenCount(185_000), "185K");
  assert.equal(formatTokenCount(1_250_000), "1.3M");
});
