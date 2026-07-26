/**
 * Shared IPC contracts.
 *
 * This module is the SINGLE source of truth for types that cross the
 * Main ↔ Preload ↔ Renderer boundary. Import with `import type` in
 * CommonJS preload modules so no runtime code is emitted.
 *
 * IMPORTANT: Do NOT import runtime-only Electron or Node modules here.
 */
/** The set of valid model IDs for runtime validation in Main. */
export const VALID_MODEL_IDS = new Set([
    "deepseek-v4-flash",
    "deepseek-v4-pro",
]);
/** IPC channel names — centralised to prevent typos and drift. */
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
    RUN_CONTINUE: "run:continue",
    // Model
    MODEL_STATUS: "model:status",
    MODEL_LIST: "model:list",
    MODEL_CONFIGURE: "model:configure",
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
};
//# sourceMappingURL=ipc-contracts.js.map