import type { ModelId } from "./constants.js";

export type { ModelId };

export interface RuntimeHealth {
  state: "ready" | "health_only";
  code?: string;
}

export type RuntimeStatus =
  | { state: "starting" }
  | {
      state: "ready";
      protocolVersion: number;
      runtimeVersion: string;
      runShell: boolean;
      modelConfigured: boolean;
      storageHealth: RuntimeHealth;
    }
  | { state: "error"; message: string };

export interface Session {
  id: string;
  workspaceRoot: string;
  title?: string;
  taskStatus: "new" | "in_progress" | "completed" | "failed" | "canceled";
  createdAt: number;
  updatedAt: number;
}

export interface SessionListResult {
  items: Session[];
}

export interface DeleteSessionResult {
  deletedSessionId: string;
}

export interface Run {
  id: string;
  sessionId: string;
  userInput?: string;
  status:
    | "queued"
    | "running"
    | "waiting_approval"
    | "waiting_user_input"
    | "finalizing"
    | "stopped"
    | "succeeded"
    | "failed"
    | "canceled"
    | "interrupted";
  modelId: ModelId;
  allowedActions?: Array<"cancel" | "approve" | "reject" | "continue">;
  modelStepCount: number;
  createdAt: number;
  startedAt?: number;
  updatedAt: number;
  completedAt?: number;
  errorCode?: string;
  cancelRequestedAt?: number;
  cancelCompletedAt?: number;
  cancelFailureCode?: string;
  pauseReason?: string;
  stopReason?: string;
  sideEffectsMayExist?: boolean;
  extensionSnapshot?: Record<string, unknown>;
  activatedTools?: string[];
}

export interface ToolProvenance {
  kind: "builtin" | "skill" | "mcp";
  sourceId: string;
  sourceVersion: string;
  contentHash: string;
  pluginId?: string;
  serverId?: string;
  skillId?: string;
}

export interface ToolCall {
  id: string;
  itemId: string;
  modelStepIndex: number;
  batchOrder: number;
  providerCallId: string;
  toolName: string;
  status: "running" | "completed" | "failed" | "canceled";
  argumentsJson?: string;
  resultJson?: string;
  approvalStatus?: "pending" | "resolved" | "canceled";
  approvalDecision?: "approve" | "reject";
  approvalFeedback?: string;
  approvalDiff?: string;
  baseSha256?: string;
  provenance?: ToolProvenance;
  toolSetHash?: string;
  startedAt: number;
  completedAt?: number;
}

export interface Item {
  id: string;
  sessionId: string;
  runId: string;
  ordinal: number;
  kind: "user_message" | "assistant_message" | "file_change" | "command_execution" | "tool_call";
  status: "in_progress" | "completed" | "failed" | "declined" | "canceled";
  createdAt: number;
  content?: string;
  incomplete?: boolean;
  completedAt?: number;
  toolCall?: ToolCall;
}

export interface SessionSnapshot {
  session: Session;
  runs: Run[];
  items: Item[];
  previousItemId?: string;
  throughEventId?: number;
}

export interface RuntimeEvent {
  eventContractVersion: 1;
  eventId: number;
  eventType: string;
  occurredAt: number;
  sessionId?: string;
  runId?: string;
  payload: Record<string, unknown>;
}

export interface EventListResult {
  items: RuntimeEvent[];
  hasMore: boolean;
  throughEventId: number;
}

export interface ModelStatus {
  provider: "deepseek";
  model: "deepseek-v4-flash";
  configured: boolean;
}

export interface ModelOption {
  id: ModelId;
  provider: "deepseek";
  displayName: string;
  configured: boolean;
  selectable: boolean;
}

export interface ModelListResult {
  models: ModelOption[];
  defaultModelId: ModelId;
}

export interface ApprovalDecision {
  decision: "approve" | "reject";
  feedback?: string;
}

interface ApprovalRequestBase {
  id: string;
  sessionId: string;
  runId: string;
  itemId: string;
  toolCallId: string;
  summary: string;
}

export interface FileApprovalRequest extends ApprovalRequestBase {
  kind: "file_change";
  diff: string;
}

export interface CommandApprovalRequest extends ApprovalRequestBase {
  kind: "command_execution";
  command: string;
  cwd: string;
  networkEnabled: false;
  timeoutSeconds: number;
}

export interface ExternalToolApprovalRequest extends ApprovalRequestBase {
  kind: "external_tool";
  toolName: string;
  arguments: Record<string, unknown>;
  provenance: ToolProvenance;
  permissionProfile: "connector" | "workspace_read";
  timeoutSeconds: number;
  envNames: string[];
}

export interface NetworkApprovalRequest extends ApprovalRequestBase {
  kind: "network_access";
  toolName: string;
  hosts: string[];
  target: string;
}

export type ApprovalRequest =
  | FileApprovalRequest
  | CommandApprovalRequest
  | ExternalToolApprovalRequest
  | NetworkApprovalRequest;

export interface PluginRecord {
  schemaVersion: 1;
  id: string;
  name: string;
  version: string;
  description: string;
  contentHash: string;
  enabled: boolean;
  status: "installed" | "removed";
  installedAt: number;
  updatedAt: number;
}

export interface PluginListResult {
  plugins: PluginRecord[];
}

export interface SkillMetadata {
  schemaVersion: 1;
  qualifiedId: string;
  name: string;
  description: string;
  pluginId: string;
  pluginVersion: string;
  pluginHash: string;
  contentHash: string;
}

export interface SkillListResult {
  skills: SkillMetadata[];
}

export interface McpServerRecord {
  schemaVersion: 1;
  pluginId: string;
  pluginVersion: string;
  pluginHash: string;
  serverId: string;
  executable: string;
  argv: string[];
  envNames: string[];
  permissionProfile: "connector" | "workspace_read";
  startupTimeoutSeconds: number;
  toolTimeoutSeconds: number;
  declaredEnabled: boolean;
  consented: boolean;
  available: boolean;
  errorCode?: string;
  updatedAt: number;
}

export interface McpListResult {
  servers: McpServerRecord[];
}

export interface ExtensionSnapshot {
  plugins: PluginRecord[];
  skills: SkillMetadata[];
  servers: McpServerRecord[];
  throughEventId: number;
}

export type RuntimeNotification =
  | { method: "session/titleUpdated"; params: { sessionId: string; title: string } }
  | { method: "run/started"; params: { sessionId: string; run: Run } }
  | { method: "run/updated"; params: { sessionId: string; run: Run } }
  | { method: "run/completed"; params: { sessionId: string; run: Run } }
  | { method: "item/started"; params: { sessionId: string; runId: string; item: Item } }
  | { method: "item/completed"; params: { sessionId: string; runId: string; item: Item } }
  | {
      method: "item/delta";
      params: { sessionId: string; runId: string; itemId: string; sequence: number; delta: string };
    }
  | {
      method: "approval/requested" | "approval/resolved" | "approval/canceled";
      params: {
        sessionId: string;
        runId: string;
        approvalId: string;
        status: "pending" | "approved" | "rejected" | "canceled" | "invalidated";
      };
    };

export type AppShortcut = "app:new-task" | "app:open-workspace";
