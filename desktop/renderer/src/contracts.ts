export type RuntimeStatus =
  | { state: "starting" }
  | {
      state: "ready";
      protocolVersion: number;
      runtimeVersion: string;
      runShell: boolean;
      modelConfigured: boolean;
    }
  | { state: "error"; message: string };

export interface Session {
  id: string;
  workspaceRoot: string;
  createdAt: number;
  updatedAt: number;
}

export interface Run {
  id: string;
  sessionId: string;
  userInput: string;
  status: "running" | "waiting_approval" | "succeeded" | "failed" | "canceled" | "interrupted";
  modelStepCount: number;
  createdAt: number;
  startedAt: number;
  updatedAt: number;
  completedAt?: number;
  errorCode?: string;
}

export interface ToolCall {
  id: string;
  itemId: string;
  toolName: string;
  status: "running" | "completed" | "failed" | "canceled";
  argumentsJson: string;
  resultJson?: string;
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
  completedAt?: number;
  toolCall?: ToolCall;
}

export interface SessionSnapshot {
  session: Session;
  runs: Run[];
  items: Item[];
  previousItemId?: string;
}

export interface ModelStatus {
  provider: "deepseek";
  model: "deepseek-v4-flash";
  configured: boolean;
}

export type RuntimeNotification =
  | { method: "run/started"; params: { sessionId: string; run: Run } }
  | { method: "run/completed"; params: { sessionId: string; run: Run } }
  | { method: "item/started"; params: { sessionId: string; runId: string; item: Item } }
  | { method: "item/completed"; params: { sessionId: string; runId: string; item: Item } }
  | { method: "item/delta"; params: { sessionId: string; runId: string; itemId: string; sequence: number; delta: string } };

declare global {
  interface Window {
    eidosRuntime: {
      getStatus: () => Promise<RuntimeStatus>;
      onStatus: (callback: (status: RuntimeStatus) => void) => () => void;
      selectWorkspace: () => Promise<string | null>;
      listSessions: () => Promise<{ items: Session[] }>;
      readSession: (sessionId: string) => Promise<SessionSnapshot>;
      createSession: (workspaceRoot: string) => Promise<Session>;
      startRun: (sessionId: string, userInput: string) => Promise<Run>;
      cancelRun: (runId: string) => Promise<Run>;
      getModelStatus: () => Promise<ModelStatus>;
      configureModel: (apiKey: string) => Promise<ModelStatus>;
      onNotification: (callback: (notification: RuntimeNotification) => void) => () => void;
    };
  }
}
