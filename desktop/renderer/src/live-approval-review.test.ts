import assert from "node:assert/strict";
import test from "node:test";
import type { RunApprovalReviewProgressParams } from "./contracts.js";
import {
  applyApprovalReviewProgress,
  clearApprovalReview,
  type LiveApprovalReviewMap,
} from "./live-approval-review.js";

const makeParams = (
  overrides: Partial<RunApprovalReviewProgressParams> = {},
): RunApprovalReviewProgressParams => ({
  sessionId: "s1",
  runId: "r1",
  itemId: "item-1",
  stage: "preparing",
  stepIndex: 1,
  totalSteps: 3,
  actionTitle: "运行探测（联网）",
  preview: "curl -sS https://api.github.com",
  ...overrides,
});

test("tracks preparing -> evaluating -> deciding -> finished and preserves startedAt", () => {
  let map: LiveApprovalReviewMap = applyApprovalReviewProgress({}, makeParams(), 100);
  assert.deepEqual(map.r1, {
    itemId: "item-1",
    stage: "preparing",
    stepIndex: 1,
    totalSteps: 3,
    actionTitle: "运行探测（联网）",
    preview: "curl -sS https://api.github.com",
    startedAt: 100,
    updatedAt: 100,
  });

  map = applyApprovalReviewProgress(
    map,
    makeParams({ stage: "evaluating", stepIndex: 2, attempt: 1 }),
    250,
  );
  assert.equal(map.r1?.startedAt, 100);
  assert.equal(map.r1?.updatedAt, 250);
  assert.equal(map.r1?.stage, "evaluating");
  assert.equal(map.r1?.attempt, 1);

  map = applyApprovalReviewProgress(
    map,
    makeParams({ stage: "deciding", stepIndex: 3, attempt: 1 }),
    600,
  );
  assert.equal(map.r1?.stage, "deciding");
  assert.equal(map.r1?.startedAt, 100);

  const finished: RunApprovalReviewProgressParams = {
    sessionId: "s1",
    runId: "r1",
    itemId: "item-1",
    stage: "finished",
    stepIndex: 3,
    totalSteps: 3,
    actionTitle: "运行探测（联网）",
  };
  map = applyApprovalReviewProgress(map, finished, 700);
  assert.equal(map.r1, undefined);
});

test("returns same map reference for identical notification and ignores stale finished", () => {
  const initial = applyApprovalReviewProgress({}, makeParams({ itemId: "item-2" }), 500);
  assert.equal(applyApprovalReviewProgress(initial, makeParams({ itemId: "item-2" }), 999), initial);

  // Finished for an older item must not drop the active item's progress
  const afterStaleFinished = applyApprovalReviewProgress(
    initial,
    makeParams({ itemId: "item-1", stage: "finished", stepIndex: 3 }),
    600,
  );
  assert.equal(afterStaleFinished, initial);
});

test("clearApprovalReview removes only the specified run", () => {
  let map = applyApprovalReviewProgress({}, makeParams({ runId: "r1" }), 100);
  map = applyApprovalReviewProgress(map, makeParams({ runId: "r2", itemId: "item-2" }), 200);
  const next = clearApprovalReview(map, "r1");
  assert.equal(next.r1, undefined);
  assert.equal(next.r2, map.r2);
  assert.equal(clearApprovalReview(next, "missing"), next);
});
