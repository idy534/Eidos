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

export interface Run {
  id: string;
  sessionId: string;
  userInput?: string;
  status: "queued" | "running" | "waiting_approval" | "waiting_user_input" | "finalizing" | "stopped" | "succeeded" | "failed" | "canceled" | "interrupted";
  modelId: ModelId;
  allowedActions?: Array<"cancel" | "approve" | "reject" | "continue">;
  modelStepCount: number;
  createdAt: number;
  startedAt?: number;
  updatedAt: number;
  completedAt?: number;
  errorCode?: string;
  pauseReason?: string;
  stopReason?: string;
  sideEffectsMayExist?: boolean;
}

export interface ToolCall {
  id: string;
  itemId: string;
  toolName: string;
  status: "running" | "completed" | "failed" | "canceled";
  argumentsJson?: string;
  resultJson?: string;
  approvalStatus?: "pending" | "resolved" | "canceled";
  approvalDecision?: "approve" | "reject";
  approvalFeedback?: string;
  approvalDiff?: string;
  baseSha256?: string;
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

export type ApprovalRequest = FileApprovalRequest | CommandApprovalRequest;

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

export interface RuntimeHealth {
  state: "ready" | "health_only";
  code?: string;
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

export type ModelId = "deepseek-v4-flash" | "deepseek-v4-pro";

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

export type RuntimeNotification =
  | { method: "run/started"; params: { sessionId: string; run: Run } }
  | { method: "run/updated"; params: { sessionId: string; run: Run } }
  | { method: "run/completed"; params: { sessionId: string; run: Run } }
  | { method: "item/started"; params: { sessionId: string; runId: string; item: Item } }
  | { method: "item/completed"; params: { sessionId: string; runId: string; item: Item } }
  | { method: "item/delta"; params: { sessionId: string; runId: string; itemId: string; sequence: number; delta: string } };

declare global {
  interface Window {
    eidosRuntime: {
      getStatus: () => Promise<RuntimeStatus>;
      getHealth: () => Promise<RuntimeHealth>;
      onStatus: (callback: (status: RuntimeStatus) => void) => () => void;
      selectWorkspace: () => Promise<string | null>;
      listSessions: () => Promise<{ items: Session[] }>;
      readSession: (sessionId: string) => Promise<SessionSnapshot>;
      listEvents: (sessionId: string, afterEventId: number) => Promise<EventListResult>;
      createSession: (workspaceRoot: string) => Promise<Session>;
      renameSession: (sessionId: string, title: string) => Promise<Session>;
      deleteSession: (sessionId: string) => Promise<{ deletedSessionId: string }>;
      startRun: (sessionId: string, userInput: string, modelId: ModelId) => Promise<Run>;
      cancelRun: (runId: string) => Promise<Run>;
      continueRun: (runId: string, userInput: string) => Promise<Run>;
      getModelStatus: () => Promise<ModelStatus>;
      listModels: () => Promise<ModelListResult>;
      configureModel: (apiKey: string) => Promise<ModelStatus>;
      listPendingApprovals: () => Promise<ApprovalRequest[]>;
      onNotification: (callback: (notification: RuntimeNotification) => void) => () => void;
      onApprovalRequest: (callback: (request: ApprovalRequest) => void) => () => void;
      respondApproval: (id: string, decision: "approve" | "reject", feedback?: string) => Promise<boolean>;
    };
  }
}
