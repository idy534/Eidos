import type { EidosRuntimeAPI } from "../../shared/index.js";

export type {
  RuntimeStatus,
  RuntimeHealth,
  ModelId,
  ModelStatus,
  ModelOption,
  ModelListResult,
  ModelProfile,
  ModelProfileDraft,
  ModelTestConnectionResult,
  WireAPI,
  Session,
  SessionListResult,
  SessionSnapshot,
  StepResolutionReview,
  DeleteSessionResult,
  Run,
  Item,
  ToolProvenance,
  ToolCall,
  RuntimeNotification,
  ApprovalDecision,
  FileApprovalRequest,
  CommandApprovalRequest,
  ExternalToolApprovalRequest,
  NetworkApprovalRequest,
  ApprovalRequest,
  PluginRecord,
  PluginListResult,
  SkillMetadata,
  SkillListResult,
  McpServerRecord,
  McpListResult,
  ExtensionSnapshot,
  RuntimeEvent,
  EventListResult,
  AppShortcut,
} from "../../shared/index.js";

declare global {
  interface Window {
    eidosRuntime: EidosRuntimeAPI;
  }
}
