/**
 * Shared IPC channel definitions.
 *
 * This module is the SINGLE source of truth for IPC channel strings.
 * Main, Preload, and Renderer MUST all import this object.
 */

export const IPC = {
  WEB_SEARCH_READ: "web-search:read",
  WEB_SEARCH_SAVE: "web-search:save",
  WEB_SEARCH_OFFICIAL_PAGE: "web-search:official-page",
  CHANNEL_READ: "channel:read",
  CHANNEL_SAVE: "channel:save",
  AUTOMATION_EXECUTE: "automation:execute",
  AUTOMATION_HISTORY: "automation:history",
  AUTOMATION_RUN_NOW: "automation:run-now",
  // Runtime
  RUNTIME_GET_STATUS: "runtime:get-status",
  RUNTIME_HEALTH: "runtime:health",
  RUNTIME_RESTART: "runtime:restart",
  RUNTIME_STATUS_EVENT: "runtime:status",
  RUNTIME_NOTIFICATION_EVENT: "runtime:notification",

  // Workspace
  WORKSPACE_SELECT: "workspace:select",
  WORKSPACE_LIST_DIRECTORY: "workspace:list-directory",
  WORKSPACE_PREVIEW_URL: "workspace:preview-url",
  WORKSPACE_RELEASE_PREVIEW: "workspace:release-preview",
  BROWSER_OPEN: "browser:open",
  BROWSER_BOUNDS: "browser:bounds",
  BROWSER_CLOSE: "browser:close",
  BROWSER_STATE: "browser:state",
  BROWSER_ANNOTATE: "browser:annotate",
  WORKSPACE_READ_FILE_PREVIEW: "workspace:read-file-preview",
  WORKSPACE_OPEN_IN_EDITOR: "workspace:open-in-editor",
  WORKSPACE_SHOW_IN_FINDER: "workspace:show-in-finder",

  // User terminal
  TERMINAL_CREATE: "terminal:create",
  TERMINAL_WRITE: "terminal:write",
  TERMINAL_RESIZE: "terminal:resize",
  TERMINAL_CLOSE: "terminal:close",
  TERMINAL_DATA_EVENT: "terminal:data",
  TERMINAL_EXIT_EVENT: "terminal:exit",

  // Session
  SIDEBAR_LIST_PINS: "sidebar:list-pins",
  SIDEBAR_SET_PINNED: "sidebar:set-pinned",
  PROJECT_CREATE: "project:create",
  PROJECT_LIST: "project:list",
  PROJECT_DELETE: "project:delete",
  SESSION_LIST: "session:list",
  SESSION_READ: "session:read",
  WORKSPACE_READ_FOR_FIND: "workspace:read-for-find",
  SEARCH_SESSIONS: "search:sessions",
  SESSION_READ_AROUND: "session:read-around",
  TOOL_TEXT_READ: "tool:read-text",
  SESSION_FORK: "session:fork",
  SESSION_CREATE: "session:create",
  SESSION_CREATE_BRANCH: "session:create-branch",
  SESSION_HANDOFF: "session:handoff",
  SESSION_RESTORE_WORKTREE: "session:restore-worktree",
  PROJECT_GIT_CONTEXT: "project:git-context",
  SESSION_RENAME: "session:rename",
  SESSION_DELETE: "session:delete",
  SESSION_GIT_STATUS: "session:git-status",
  SESSION_GIT_DIFF: "session:git-diff",
  SESSION_GIT_SWITCH_BRANCH: "session:git-switch-branch",
  SESSION_GIT_CREATE_BRANCH: "session:git-create-branch",
  SESSION_GIT_STAGE: "session:git-stage",
  SESSION_GIT_COMMIT: "session:git-commit",
  REVIEW_LIST_COMMENTS: "review:list-comments",
  REVIEW_CREATE_COMMENT: "review:create-comment",
  REVIEW_DELETE_COMMENT: "review:delete-comment",
  SESSION_GIT_REMOTE_STATUS: "session:git-remote-status",
  SESSION_GIT_FETCH: "session:git-fetch",
  SESSION_GIT_PULL: "session:git-pull",
  SESSION_GIT_PUSH: "session:git-push",
  SESSION_GIT_MERGE: "session:git-merge",
  SESSION_GIT_MERGE_ABORT: "session:git-merge-abort",
  SESSION_GIT_REBASE: "session:git-rebase",
  SESSION_GIT_REBASE_CONTINUE: "session:git-rebase-continue",
  SESSION_GIT_REBASE_ABORT: "session:git-rebase-abort",

  // Worktree settings
  WORKTREE_SETTINGS_READ: "worktree-settings:read",
  WORKTREE_SETTINGS_UPDATE: "worktree-settings:update",

  // Events
  EVENT_LIST: "event:list",

  // Run
  INPUT_QUOTE: "input:quote",
  INPUT_PICK: "input:pick",
  INPUT_PREPARE: "input:prepare",
  INPUT_READ: "input:read",
  INPUT_PREVIEW_URL: "input:preview-url",
  INPUT_PASTE_IMAGE: "input:pasteImage",
  INPUT_DRAFT_READ: "input:draftRead",
  INPUT_DRAFT_WRITE: "input:draftWrite",
  AGENT_CATALOG: "agent:catalog",
  AGENT_DEFINITION_READ: "agent:definition-read",
  AGENT_DEFINITION_SAVE: "agent:definition-save",
  AGENT_DEFINITION_REMOVE: "agent:definition-remove",
  AGENT_READ: "agent:read",
  AGENT_STOP: "agent:stop",
  MEMORY_BACKUP: "memory:backup",
  MEMORY_REQUEST: "memory:request",
  PLANNING_READ: "planning:read",
  PLANNING_ANSWER: "planning:answer",
  PLAN_READ: "plan:read",
  PLAN_EDIT: "plan:edit",
  PLAN_OPEN: "plan:open",
  RUN_START: "run:start",
  RUN_CANCEL: "run:cancel",
  RUN_RESUME: "run:resume",
  RUN_UPDATE_QUEUED: "run:update-queued",
  RUN_LIST_QUEUED: "run:list-queued",
  RUN_STEER_QUEUED: "run:steer-queued",
  RUN_DELETE_QUEUED: "run:delete-queued",
  RUN_REVISE: "run:revise",
  CONTEXT_USAGE: "context:usage",
  CONTEXT_DETAILS: "context:details",
  CONTEXT_COMPACT: "context:compact",

  // Response actions
  RESPONSE_ACTION_STATE: "response-action:state",
  ITEM_SET_FEEDBACK: "item:set-feedback",

  // Model
  MODEL_DISCOVER_CUSTOM: "model:discoverCustom",
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
  SKILL_DETAIL: "skill:detail",
  SKILL_SET_ENABLED: "skill:set-enabled",
  SKILL_REMOVE: "skill:remove",

  // MCP
  MCP_LIST: "mcp:list",
  MCP_SET_ENABLED: "mcp:set-enabled",
  MCP_CREATE: "mcp:create",
  MCP_UPDATE: "mcp:update",
  MCP_REMOVE: "mcp:remove",

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
