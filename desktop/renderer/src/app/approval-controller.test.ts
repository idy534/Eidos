import test from "node:test";
import assert from "node:assert/strict";
import type { ApprovalRequest } from "../contracts.js";
import { approvalReducer, initialApprovalState } from "./useApprovalController.js";
import { MAX_APPROVAL_FEEDBACK_BYTES } from "../../../shared/constants.js";

function makeApproval(id: string, runId = "run-1"): ApprovalRequest {
  return {
    id,
    sessionId: "s-1",
    runId,
    itemId: `item-${id}`,
    toolCallId: `tc-${id}`,
    kind: "file_change",
    summary: `Change ${id}`,
    diff: "+ line",
  };
}

test("approvalReducer adds and merges approvals without duplicates", () => {
  const app1 = makeApproval("app-1");
  const app2 = makeApproval("app-2");

  let state = approvalReducer(initialApprovalState, { type: "added", approval: app1 });
  assert.equal(state.approvals.length, 1);
  assert.equal(state.approvals[0].id, "app-1");

  state = approvalReducer(state, { type: "merge", approvals: [app1, app2] });
  assert.equal(state.approvals.length, 2);
});

test("approvalReducer response_expired marks approval as expired and sets error message", () => {
  const app1 = makeApproval("app-1");
  let state = approvalReducer(initialApprovalState, { type: "added", approval: app1 });
  state = approvalReducer(state, { type: "response_started", approvalId: "app-1", kind: "approve" });
  assert.equal(state.respondingApprovalIds.has("app-1"), true);

  state = approvalReducer(state, {
    type: "response_expired",
    approvalId: "app-1",
    error: "该审批已过期或已被处理。",
  });

  assert.equal(state.respondingApprovalIds.has("app-1"), false);
  assert.equal(state.expiredApprovalIds.has("app-1"), true);
  assert.equal(state.errorsByApprovalId["app-1"], "该审批已过期或已被处理。");
});

test("approvalReducer run_completed removes approvals, responding state, errors, and expired state atomically", () => {
  const app1 = makeApproval("app-1", "run-1");
  const app2 = makeApproval("app-2", "run-2");

  let state = approvalReducer(initialApprovalState, { type: "merge", approvals: [app1, app2] });
  state = approvalReducer(state, { type: "response_expired", approvalId: "app-1", error: "Expired" });
  state = approvalReducer(state, { type: "dialog_opened", approval: app1 });

  assert.equal(state.approvals.length, 2);
  assert.equal(state.expiredApprovalIds.has("app-1"), true);
  assert.equal(state.feedbackDialogApproval?.id, "app-1");

  // Complete run-1
  state = approvalReducer(state, { type: "run_completed", runId: "run-1" });

  assert.equal(state.approvals.length, 1);
  assert.equal(state.approvals[0].id, "app-2");
  assert.equal(state.expiredApprovalIds.has("app-1"), false);
  assert.equal(state.errorsByApprovalId["app-1"], undefined);
  assert.equal(state.feedbackDialogApproval, null);
});

test("approvalReducer preserves immutability and returns new references on change", () => {
  const app1 = makeApproval("app-1");
  const s1 = approvalReducer(initialApprovalState, { type: "added", approval: app1 });
  assert.notEqual(s1, initialApprovalState);
  assert.notEqual(s1.approvals, initialApprovalState.approvals);

  const s2 = approvalReducer(s1, { type: "response_started", approvalId: "app-1", kind: "reject" });
  assert.notEqual(s2.respondingApprovalIds, s1.respondingApprovalIds);
});

test("MAX_APPROVAL_FEEDBACK_BYTES utf8 byte calculation boundary", () => {
  const shortText = "OK";
  const shortBytes = new TextEncoder().encode(shortText).byteLength;
  assert.ok(shortBytes <= MAX_APPROVAL_FEEDBACK_BYTES);

  const unicodeText = "测试".repeat(1000); // 3000 bytes
  const unicodeBytes = new TextEncoder().encode(unicodeText).byteLength;
  assert.ok(unicodeBytes > MAX_APPROVAL_FEEDBACK_BYTES);
});
