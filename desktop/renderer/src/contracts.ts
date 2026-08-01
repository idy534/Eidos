import type { EidosRuntimeAPI } from "../../shared/index.js";

export type {
  RuntimeStatus,
  RuntimeHealth,
  ModelId,
  ModelOption,
  ModelListResult,
  ModelReasoning,
  ModelPresetModel,
  ModelProviderPreset,
  ModelPresetsResult,
  ModelCreateInput,
  ModelUpdateInput,
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
