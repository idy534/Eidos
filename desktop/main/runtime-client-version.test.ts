import assert from "node:assert/strict";
import test from "node:test";

import { buildInitializeParams, UNSPECIFIED_CLIENT_VERSION } from "./runtime-client.js";
import { clearRuntimeState, type PendingApprovalEntry, type RuntimeActiveRunEntry } from "./runtime-process-state.js";

test("buildInitializeParams uses supplied clientVersion instead of hardcoded 0.3.0", () => {
  assert.deepEqual(buildInitializeParams("0.5.7"), {
    client: { name: "eidos-desktop", version: "0.5.7" },
    protocolVersion: 1,
  });
  assert.equal(buildInitializeParams("").client.version, UNSPECIFIED_CLIENT_VERSION);
});

test("clearRuntimeState clears active runs and settles pending approvals", () => {
  const activeRuns = new Map<string, RuntimeActiveRunEntry>([
    ["run-1", { sessionId: "session-1", status: "running" }],
  ]);
  const decisions: string[] = [];
  const pendingApprovals = new Map<string, PendingApprovalEntry>([
    [
      "approval-1",
      {
        request: {
          id: "approval-1",
          sessionId: "session-1",
          runId: "run-1",
          itemId: "item-1",
          toolCallId: "tool-1",
          kind: "command_execution",
          summary: "Run shell",
          command: "echo hi",
          cwd: ".",
          networkEnabled: false,
          timeoutSeconds: 10,
        },
        resolve: (value) => decisions.push(value.decision),
      },
    ],
  ]);

  clearRuntimeState(activeRuns, pendingApprovals);

  assert.equal(activeRuns.size, 0);
  assert.equal(pendingApprovals.size, 0);
  assert.deepEqual(decisions, ["reject"]);
});
