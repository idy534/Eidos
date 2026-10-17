import type { AgentCatalogResponse, AgentSummary, CollaborationState } from "./collaboration.generated.js";
import { isRunTermination } from "./run-termination.js";

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
    && (text(value.role, 64) && /^[a-z][a-z0-9_-]*$/.test(value.role))
    && (value.baseRole == null || ["default", "explorer", "worker"].includes(String(value.baseRole)))
    && optionalText(value.modelId, 256) && optionalText(value.providerId, 64)
    && (value.definitionVersion == null || (text(value.definitionVersion, 64) && /^[a-f0-9]{64}$/.test(value.definitionVersion)))
    && (value.requiredForCompletion === undefined || typeof value.requiredForCompletion === "boolean")
    && ["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing", "succeeded", "failed", "stopped", "canceled", "interrupted"].includes(String(value.status))
    && text(value.task, 512) && optionalText(value.result, 2000)
    && optionalText(value.resultItemId) && optionalText(value.errorCode, 1024)
    && optionalText(value.stopReason, 128)
    && (value.termination == null || isRunTermination(value.termination))
    && (value.reconciliationRequired === undefined || typeof value.reconciliationRequired === "boolean")
    && Number.isSafeInteger(value.createdAt);
}
export function isCollaborationState(value: unknown): value is CollaborationState {
  return record(value) && optionalText(value.parentRunId)
    && Array.isArray(value.agents) && value.agents.length <= 16 && value.agents.every(agent)
    && Array.isArray(value.messages) && value.messages.length <= 16 && value.messages.every((message) =>
      record(message) && [message.id, message.senderSessionId, message.recipientSessionId].every((v) => text(v) && v.length > 0)
      && text(message.content, 2000) && Number.isSafeInteger(message.createdAt));
}

export function isAgentCatalog(value: unknown): value is AgentCatalogResponse {
  return record(value) && Object.keys(value).every((key) => ["agents", "error"].includes(key))
    && optionalText(value.error, 512) && Array.isArray(value.agents) && value.agents.length <= 128
    && value.agents.every((item) => record(item)
      && Object.keys(item).every((key) => ["name", "description", "source", "baseRole", "providerId", "modelId", "enabled", "available", "reason", "definitionVersion"].includes(key))
      && text(item.name, 256) && text(item.description, 1000) && text(item.source, 4096)
      && ["default", "explorer", "worker"].includes(String(item.baseRole))
      && typeof item.enabled === "boolean" && typeof item.available === "boolean"
      && optionalText(item.providerId, 64) && optionalText(item.modelId, 256)
      && optionalText(item.reason, 512)
      && (item.definitionVersion == null || (text(item.definitionVersion, 64) && /^[a-f0-9]{64}$/.test(item.definitionVersion))));
}
