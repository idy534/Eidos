import type { AgentCatalogResponse, AgentChanges, AgentChangesRequest, AgentMessageFromUserRequest, AgentSummary, CollaborationState } from "./collaboration.generated.js";
import { isRunTermination } from "./run-termination.js";

export const BUILTIN_AGENT_ROLES: readonly string[] = ["default", "explorer", "worker", "verifier"];

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
// Python truncates by code point (SQLite substr / slicing), so count code points here;
// String.length counts UTF-16 units and would reject a 2000-code-point reply containing emoji.
function codePointLength(value: string): number {
  let count = 0;
  for (const _ of value) count += 1;
  return count;
}
function text(value: unknown, max = 128): value is string {
  return typeof value === "string" && value.length <= max * 2 && codePointLength(value) <= max;
}
function optionalText(value: unknown, max = 128): boolean {
  return value == null || text(value, max);
}
function agent(value: unknown): value is AgentSummary {
  return record(value)
    && [value.id, value.taskName, value.parentRunId, value.sessionId, value.runId].every((v) => text(v) && v.length > 0)
    && (text(value.role, 64) && /^[a-z][a-z0-9_-]*$/.test(value.role))
    && (value.baseRole == null || BUILTIN_AGENT_ROLES.includes(String(value.baseRole)))
    && optionalText(value.modelId, 256) && optionalText(value.providerId, 64)
    && (value.definitionVersion == null || (text(value.definitionVersion, 64) && /^[a-f0-9]{64}$/.test(value.definitionVersion)))
    && (value.requiredForCompletion === undefined || typeof value.requiredForCompletion === "boolean")
    && ["queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing", "succeeded", "failed", "stopped", "canceled", "interrupted"].includes(String(value.status))
    && text(value.task, 512) && optionalText(value.result, 2000)
    && optionalText(value.resultItemId) && optionalText(value.errorCode, 1024)
    && optionalText(value.stopReason, 128)
    && (value.termination == null || isRunTermination(value.termination))
    && (value.reconciliationRequired === undefined || typeof value.reconciliationRequired === "boolean")
    && optionalText(value.userInstruction, 500)
    && (value.workspaceMode === undefined || value.workspaceMode === "shared" || value.workspaceMode === "worktree")
    && (value.usage == null || usage(value.usage))
    && Number.isSafeInteger(value.createdAt);
}
function count(value: unknown): boolean {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}
function usage(value: unknown): boolean {
  return record(value)
    && ["inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "effectiveMs"].every((key) => value[key] === undefined || count(value[key]))
    && (value.incomplete === undefined || typeof value.incomplete === "boolean");
}
export function isAgentMessageFromUserRequest(value: unknown): value is AgentMessageFromUserRequest {
  return record(value) && Object.keys(value).every((key) => ["operationId", "parentRunId", "agentId", "text"].includes(key))
    && [value.operationId, value.parentRunId, value.agentId].every((v) => text(v) && v.length > 0)
    && typeof value.text === "string" && value.text.trim().length > 0 && new TextEncoder().encode(value.text).byteLength <= 64 * 1024;
}
export function isAgentChangesRequest(value: unknown): value is AgentChangesRequest {
  return record(value) && Object.keys(value).every((key) => ["parentRunId", "agentId", "path", "offsetBytes", "maxBytes"].includes(key))
    && [value.parentRunId, value.agentId].every((v) => text(v) && v.length > 0)
    && optionalText(value.path, 4096)
    && (value.offsetBytes === undefined || count(value.offsetBytes))
    && (value.maxBytes === undefined || (Number.isSafeInteger(value.maxBytes) && Number(value.maxBytes) >= 4 && Number(value.maxBytes) <= 64 * 1024));
}
export function isAgentChanges(value: unknown): value is AgentChanges {
  return record(value) && text(value.agentId) && value.agentId.length > 0
    && ["ready", "stale", "integrated", "partially_integrated", "discarded"].includes(String(value.status))
    && Array.isArray(value.files) && value.files.length <= 2000 && value.files.every((file) => record(file)
      && text(file.path, 4096) && ["added", "modified", "deleted"].includes(String(file.kind))
      && (file.additions === undefined || count(file.additions)) && (file.deletions === undefined || count(file.deletions))
      && (file.binary === undefined || typeof file.binary === "boolean"))
    && (value.diff === undefined || (typeof value.diff === "string" && new TextEncoder().encode(value.diff).byteLength <= 64 * 1024))
    && (value.diffNextOffset === undefined || count(value.diffNextOffset))
    && (value.diffHasMore === undefined || typeof value.diffHasMore === "boolean");
}
export function isCollaborationState(value: unknown): value is CollaborationState {
  return record(value) && optionalText(value.parentRunId)
    && Array.isArray(value.agents) && value.agents.length <= 16 && value.agents.every(agent)
    && Array.isArray(value.messages) && value.messages.length <= 32 && value.messages.every((message) =>
      record(message) && [message.id, message.senderSessionId, message.recipientSessionId].every((v) => text(v) && v.length > 0)
      && (message.senderKind === undefined || message.senderKind === "agent" || message.senderKind === "user")
      && text(message.content, 64 * 1024) && new TextEncoder().encode(message.content).byteLength <= 64 * 1024
      && Number.isSafeInteger(message.createdAt));
}

export function isAgentCatalog(value: unknown): value is AgentCatalogResponse {
  return record(value) && Object.keys(value).every((key) => ["agents", "error"].includes(key))
    && optionalText(value.error, 512) && Array.isArray(value.agents) && value.agents.length <= 128
    && value.agents.every((item) => record(item)
      && Object.keys(item).every((key) => ["name", "description", "source", "baseRole", "providerId", "modelId", "enabled", "available", "reason", "definitionVersion"].includes(key))
      && text(item.name, 256) && text(item.description, 1000) && text(item.source, 4096)
      && BUILTIN_AGENT_ROLES.includes(String(item.baseRole))
      && typeof item.enabled === "boolean" && typeof item.available === "boolean"
      && optionalText(item.providerId, 64) && optionalText(item.modelId, 256)
      && optionalText(item.reason, 512)
      && (item.definitionVersion == null || (text(item.definitionVersion, 64) && /^[a-f0-9]{64}$/.test(item.definitionVersion))));
}
