/**
 * Shared IPC channel definitions.
 *
 * This module is the SINGLE source of truth for IPC channel strings.
 * Main, Preload, and Renderer MUST all import this object.
 */

export const IPC = {
  // Runtime
  RUNTIME_GET_STATUS: "runtime:get-status",
  RUNTIME_HEALTH: "runtime:health",
  RUNTIME_STATUS_EVENT: "runtime:status",
  RUNTIME_NOTIFICATION_EVENT: "runtime:notification",

  // Workspace
  WORKSPACE_SELECT: "workspace:select",

  // Session
  SESSION_LIST: "session:list",
  SESSION_READ: "session:read",
  SESSION_CREATE: "session:create",
  SESSION_RENAME: "session:rename",
  SESSION_DELETE: "session:delete",

  // Events
  EVENT_LIST: "event:list",

  // Run
  RUN_START: "run:start",
  RUN_CANCEL: "run:cancel",

  // Model
  MODEL_PRESETS: "model:presets",
  MODEL_LIST: "model:list",
  MODEL_CREATE: "model:create",
  MODEL_UPDATE: "model:update",
  MODEL_DELETE: "model:delete",

  // Plugin
  PLUGIN_LIST: "plugin:list",
  PLUGIN_IMPORT: "plugin:import",
  PLUGIN_SET_ENABLED: "plugin:set-enabled",
  PLUGIN_REMOVE: "plugin:remove",

  // Skill
  SKILL_LIST: "skill:list",

  // MCP
  MCP_LIST: "mcp:list",
  MCP_SET_ENABLED: "mcp:set-enabled",

  // Extensions
  EXTENSION_READ: "extension:read",
  EXTENSION_READ_EVENTS: "extension:read-events",

  // Approval
  APPROVAL_LIST: "approval:list",
  APPROVAL_RESPOND: "approval:respond",
  APPROVAL_REQUESTED_EVENT: "approval:requested",

  // App-level shortcuts sent from Main → Renderer
  APP_NEW_TASK: "app:new-task",
  APP_OPEN_WORKSPACE: "app:open-workspace",
} as const;

export type IPCChannel = (typeof IPC)[keyof typeof IPC];
