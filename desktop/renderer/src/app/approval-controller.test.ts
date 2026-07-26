import { test } from "node:test";
import assert from "node:assert/strict";
import type { ApprovalRequest } from "../contracts.js";
import { MAX_APPROVAL_FEEDBACK_BYTES } from "../../../shared/constants.js";

const mockApproval1: ApprovalRequest = {
  id: "app-1",
  sessionId: "session-1",
  runId: "run-1",
  itemId: "item-1",
  toolCallId: "tc-1",
  kind: "command_execution",
  summary: "Run shell command",
  command: "ls -la",
  cwd: "/workspace",
  timeoutSeconds: 30,
};

const mockApproval2: ApprovalRequest = {
  id: "app-2",
  sessionId: "session-1",
  runId: "run-1",
  itemId: "item-2",
  toolCallId: "tc-2",
  kind: "network_access",
  summary: "Access external network",
  toolName: "fetch_url",
  target: "https://api.github.com",
  hosts: ["api.github.com"],
};

void test("MAX_APPROVAL_FEEDBACK_BYTES utf8 byte calculation boundary", () => {
  const shortText = "OK";
  const shortBytes = new TextEncoder().encode(shortText).byteLength;
  assert.ok(shortBytes <= MAX_APPROVAL_FEEDBACK_BYTES);

  const unicodeText = "测试".repeat(1000); // 3000 bytes
  const unicodeBytes = new TextEncoder().encode(unicodeText).byteLength;
  assert.ok(unicodeBytes > MAX_APPROVAL_FEEDBACK_BYTES);
});

void test("Approval list ordering and deduplication", () => {
  const approvals: ApprovalRequest[] = [];

  const add = (req: ApprovalRequest) => {
    const idx = approvals.findIndex((a) => a.id === req.id);
    if (idx >= 0) {
      approvals[idx] = req;
    } else {
      approvals.push(req);
    }
  };

  add(mockApproval1);
  add(mockApproval2);
  assert.equal(approvals.length, 2);

  // Duplicate add replaces item
  const updated1 = { ...mockApproval1, summary: "Updated summary" };
  add(updated1);
  assert.equal(approvals.length, 2);
  assert.equal(approvals[0]?.summary, "Updated summary");
});
