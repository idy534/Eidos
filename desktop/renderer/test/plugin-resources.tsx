import { useState } from "react";
import { createRoot } from "react-dom/client";
import { SettingsPage } from "../src/components/settings/SettingsPage.js";
import type { EidosRuntimeAPI, McpServerRecord, PluginRecord, SkillMetadata } from "../src/contracts.js";
import "../src/styles.css";
import "../src/components/settings/SettingsPage.css";

const plugin: PluginRecord = {
  schemaVersion: 1, id: "checks", name: "代码质量检查", version: "1.0.0", description: "此插件包含代码审查 Skill、工具服务和任务完成检查。",
  contentHash: "a".repeat(64), enabled: true, status: "installed", installedAt: 1, updatedAt: 1,
  presentation: { developerName: "Eidos Plugins", license: "Apache-2.0", onboardingSkillId: "checks:review" },
  compatibility: { format: "codex", declaredVersion: "1.0.0", issues: ["codex_apps_not_supported"] },
  hookReview: { contentHash: "a".repeat(64), trusted: false, executableCount: 1,
    installedRoot: "/plugins/checks", dataDirectory: "/plugins/checks/hook-data",
    definitions: [{ key: "stop", event: "Stop", handlerType: "command", command: '"$RUNTIME_PYTHON" "$PLUGIN_ROOT/hooks/check_workspace_before_completion.py"' }] },
};
const initialSkill: SkillMetadata = {
  schemaVersion: 1, qualifiedId: "checks:review", name: "代码审查", description: "检查代码的正确性、权限、取消和恢复，并给出有证据的问题。",
  sourceKind: "plugin", pluginId: "checks", pluginVersion: "1.0.0", pluginHash: plugin.contentHash,
  contentHash: "b".repeat(64), enabled: true, available: true,
};
const mcp: McpServerRecord = {
  schemaVersion: 1, pluginId: "checks", pluginVersion: "1.0.0", pluginHash: plugin.contentHash, serverId: "local-quality-tools",
  executable: "python3", argv: ["tools.py"], envNames: [], permissionProfile: "workspace_read", startupTimeoutSeconds: 5,
  toolTimeoutSeconds: 10, declaredEnabled: true, consented: false, available: false, updatedAt: 1,
};
const github: PluginRecord = { schemaVersion: 1, id: "eidos-github", name: "GitHub", version: "1.0.0",
  description: "连接 GitHub，读取仓库、Issue 和 Pull Request，并在授权后执行操作。", contentHash: "c".repeat(64), enabled: true,
  status: "installed", installedAt: 1, updatedAt: 1, presentation: { developerName: "Eidos" } };
const remote: McpServerRecord = { ...mcp, pluginId: github.id, pluginHash: github.contentHash, serverId: "github", executable: "", argv: [],
  permissionProfile: "connector", remote: { url: "https://api.githubcopilot.com/mcp/", authType: "github-device" } };
let builtinInstalled = false;
let connected = false;
let hookTrusted = false;
Object.assign(window, { eidosRuntime: {
  hookConfiguration: async (request: { action?: string; trusted?: boolean }) => {
    if (request.action === "trust") hookTrusted = request.trusted ?? false;
    return { sources: [{ id: "user:fixture", scope: "user", paths: ["/Users/example/Library/Application Support/Eidos/hooks.json"], contentHash: "d".repeat(64),
      enabled: true, trusted: hookTrusted, definitions: [{ key: "start", event: "SessionStart", handlerType: "command",
        command: '"$RUNTIME_PYTHON" "$PLUGIN_ROOT/check_workspace_conventions_before_session_start.py"' }] }] };
  },
  readHookHistory: async (request: { cursor?: number }) => ({ items: [{ id: request.cursor ? "older" : "latest", runId: "run", sessionId: "session", sourceId: "checks",
    event: request.cursor ? "Stop" : "SessionStart", handlerType: "command", status: "completed", createdAt: 1,
    result: { status: "completed", systemMessage: "检查提醒：请阅读结果。" }, stdoutPreview: "long-output-".repeat(300), previewTruncated: true }], nextCursor: request.cursor ? null : 9 }),
  readSkillDetail: async () => ({ qualifiedId: initialSkill.qualifiedId, content: "# 审查清单\n逐项检查当前代码。", body: "# 审查清单\n逐项检查当前代码。", directory: "/plugins/checks/skills/review" }),
  readMcpTools: async () => ({ state: "not_authorized", tools: [] }),
  builtinPlugins: async () => ({ plugins: [{ id: github.id, name: "GitHub", description: github.description, version: "1.0.0", installed: builtinInstalled }] }),
  mcpConnection: async (request: { action?: string }) => {
    if (request.action === "connect") connected = true;
    if (request.action === "disconnect") connected = false;
    return { pluginId: github.id, serverId: "github", status: connected ? "connected" : "disconnected",
      accountLabel: connected ? "octocat" : null, authType: "github-device", browserAvailable: false, updatedAt: 1 };
  },
  openMcpLogin: async () => {},
} as unknown as EidosRuntimeAPI });

function Fixture() {
  const [skill, setSkill] = useState(initialSkill);
  const [installed, setInstalled] = useState(false);
  const [, refresh] = useState(0);
  return <SettingsPage initialCategory="plugins"
    runtime={{ state: "ready", protocolVersion: 1, runtimeVersion: "0.5.4", runShell: true, modelConfigured: true, storageHealth: { state: "ready" } }}
    plugins={[plugin, ...(installed ? [github] : [])]} skills={[skill]} mcpServers={[mcp, ...(installed ? [{ ...remote, consented: connected, available: connected }] : [])]} pendingAction={undefined}
    onInstallBuiltinPlugin={async () => { builtinInstalled = true; setInstalled(true); }}
    onExtensionsChanged={async () => refresh(value => value + 1)}
    onClose={() => {}} onModelsChanged={async () => {}} onImportPlugin={async () => {}} onTogglePlugin={async () => {}}
    onTrustPluginHooks={async () => {}} onRemovePlugin={async () => {}}
    onToggleSkill={async (_id, enabled) => setSkill(value => ({ ...value, enabled }))}
    onRemoveSkill={async id => ({ qualifiedId: id, removed: true, cleanupPending: false })}
    onToggleMcp={async () => {}} onCreateMcp={async () => {}} onUpdateMcp={async () => {}} onRemoveMcp={async () => {}} />;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
