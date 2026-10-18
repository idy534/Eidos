import assert from "node:assert/strict";
import test from "node:test";
import type { ContextCompactionActivity } from "../../shared/context.generated.js";
import type { SessionSnapshot } from "./contracts.js";
import { mergeContextCompactions } from "./context-compaction.js";
import { applyNotification } from "./session-state.js";

const activity: ContextCompactionActivity = { requestId: "compact", runId: "run", eventId: 2, startedAt: 10, updatedAt: 10, status: "running", trigger: "manual" };

test("compaction updates retain their position and ignore delayed starts and repeated events", () => {
  const finished = { ...activity, eventId: 4, status: "succeeded" as const, startedAt: 20, updatedAt: 20 };
  const merged = mergeContextCompactions([activity], [finished]);
  assert.equal(merged[0]?.startedAt, 10);
  assert.equal(merged[0]?.status, "succeeded");
  assert.deepEqual(mergeContextCompactions(merged, [activity, finished]), merged);
});

test("compaction events are scoped to the selected Session, Run and snapshot waterline", () => {
  const snapshot: SessionSnapshot = {
    session: { id: "session", workspaceRoot: "/fixture", taskStatus: "in_progress", createdAt: 1, updatedAt: 1 },
    runs: [{ id: "run", sessionId: "session", status: "running", modelId: "fixture", modelStepCount: 1, createdAt: 1, updatedAt: 1 }],
    items: [], stepResolutions: [], throughEventId: 1,
  };
  const notify = (sessionId: string, entry = activity) => ({ method: "context/compactionUpdated" as const, params: { sessionId, activity: entry } });
  assert.equal(applyNotification(snapshot, notify("other")), snapshot);
  assert.equal(applyNotification(snapshot, notify("session", { ...activity, runId: "other" })), snapshot);
  assert.equal(applyNotification(snapshot, notify("session", { ...activity, eventId: 1 })), snapshot);
  const next = applyNotification(snapshot, notify("session"))!;
  assert.equal(next.contextCompactions?.length, 1);
  assert.equal(applyNotification(next, notify("session"))?.contextCompactions?.length, 1);
});
