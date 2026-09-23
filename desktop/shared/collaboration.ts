import type { AgentSummary, CollaborationState } from "./collaboration.generated.js";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function text(value: unknown, max = 128): value is string {
  return typeof value === "string" && value.length <= max;
}
function optionalText(value: unknown, max = 128): boolean {
  return value == null || text(value, max);
}
function agent(value: unknown): value is AgentSummary {
  return record(value)
    && [value.id, value.taskName, value.parentRunId, value.sessionId, value.runId].every((v) => text(v) && v.length > 0)
    && ["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing", "succeeded", "failed", "stopped", "canceled", "interrupted"].includes(String(value.status))
    && text(value.task, 512) && optionalText(value.result, 2000)
    && optionalText(value.resultItemId) && optionalText(value.errorCode, 1024)
    && Number.isSafeInteger(value.createdAt);
}
export function isCollaborationState(value: unknown): value is CollaborationState {
  return record(value) && optionalText(value.parentRunId)
    && Array.isArray(value.agents) && value.agents.length <= 16 && value.agents.every(agent)
    && Array.isArray(value.messages) && value.messages.length <= 16 && value.messages.every((message) =>
      record(message) && [message.id, message.senderSessionId, message.recipientSessionId].every((v) => text(v) && v.length > 0)
      && text(message.content, 2000) && Number.isSafeInteger(message.createdAt));
}
