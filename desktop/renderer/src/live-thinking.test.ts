import assert from "node:assert/strict";
import test from "node:test";
import { applyThinking, clearThinking, MAX_PREVIOUS_THINKING, type LiveThinkingMap } from "./live-thinking.js";

const params = (summaryId: string, title: string, body?: string) =>
  ({ sessionId: "s", runId: "r", summaryId, title, ...(body === undefined ? {} : { body }) });

test("an update to the same part replaces it without adding history", () => {
  let map: LiveThinkingMap = applyThinking({}, params("a:0", "Check"), 100);
  const first = map.r?.receivedAt;
  map = applyThinking(map, params("a:0", "Check", "more"), 900);
  assert.deepEqual(map.r, { summaryId: "a:0", title: "Check", body: "more", receivedAt: map.r?.receivedAt, previous: [] });
  assert.equal(map.r?.receivedAt, first);
});

test("a new part moves the old title into bounded history", () => {
  let map: LiveThinkingMap = {};
  for (let index = 0; index < MAX_PREVIOUS_THINKING + 3; index += 1) {
    map = applyThinking(map, params(`a:${index}`, `T${index}`));
  }
  assert.equal(map.r?.title, `T${MAX_PREVIOUS_THINKING + 2}`);
  assert.equal(map.r?.previous.length, MAX_PREVIOUS_THINKING);
  assert.equal(map.r?.previous.at(-1), `T${MAX_PREVIOUS_THINKING + 1}`);
});

test("an identical notification keeps the same object and runs are independent", () => {
  const map = applyThinking({}, params("a:0", "Check", "x"));
  assert.equal(applyThinking(map, params("a:0", "Check", "x")), map);
  const other = applyThinking(map, { ...params("b:0", "Other"), runId: "r2" });
  assert.equal(other.r, map.r);
  assert.equal(clearThinking(other, "r").r, undefined);
  assert.equal(clearThinking(other, "missing"), other);
});
