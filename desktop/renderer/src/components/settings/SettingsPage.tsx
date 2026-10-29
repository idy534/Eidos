import { useCallback, useEffect, useState } from "react";
import type {
  ModelListResult,
  McpServerRecord,
  McpCreateInput,
  McpUpdateInput,
  PluginRecord,
  RuntimeStatus,
  SkillMetadata,
  SkillRemoval,
} from "../../contracts.js";
import type { SettingsCategory, SettingsPendingAction, SettingsToast } from "./settings-types.js";
import { SettingsCategoryItem, SettingsNavGroup, SettingsNavigation } from "./SettingsNavigation.js";
import { ModelSettings } from "./ModelSettings.js";
import { PluginSettings } from "./PluginSettings.js";
import { SkillSettings } from "./SkillSettings.js";
import { McpSettings } from "./McpSettings.js";
import { RuntimeSettings } from "./RuntimeSettings.js";
import { MemorySettings } from "./MemorySettings.js";
import { WebSearchSettings } from "./WebSearchSettings.js";
import { ChannelSettings } from "./ChannelSettings.js";
import { WorktreeSettings } from "./WorktreeSettings.js";
import { AgentSettings } from "./AgentSettings.js";
import { PersonalizationSettings, discardPersonalizationDraft } from "./PersonalizationSettings.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { Button } from "../Button.js";
import { ToastContainer, type ToastItem } from "../ToastContainer.js";

interface SettingsPageProps {
  initialCategory?: SettingsCategory;
  sessionId?: string | undefined;
  onOpenMemorySource?: ((id: string) => void) | undefined;
  runtime: RuntimeStatus;
  modelList?: ModelListResult | undefined;
  modelLoading?: boolean | undefined;
  modelError?: string | undefined;
  plugins: PluginRecord[];
  skills: SkillMetadata[];
  mcpServers: McpServerRecord[];
  extensionError?: string | undefined;
  pendingAction: SettingsPendingAction;
  pendingSkillIds?: ReadonlySet<string> | undefined;
  hasBlockingModal?: boolean | undefined;
  onClose: () => void;
  onModelsChanged: () => Promise<void>;
  onImportPlugin: () => Promise<PluginRecord | null>;
  onInstallBuiltinPlugin?: ((pluginId: string) => Promise<void>) | undefined;
  onExtensionsChanged?: (() => Promise<void>) | undefined;
  onTogglePlugin: (pluginId: string, enabled: boolean) => Promise<void>;
  onTrustPluginHooks?: ((pluginId: string, contentHash: string, trusted: boolean) => Promise<void>) | undefined;
  onRemovePlugin: (pluginId: string) => Promise<void>;
  onToggleSkill: (qualifiedId: string, enabled: boolean) => Promise<void>;
  onRemoveSkill: (qualifiedId: string) => Promise<SkillRemoval>;
  onToggleMcp: (pluginId: string, serverId: string, enabled: boolean) => Promise<void>;
  onCreateMcp: (input: McpCreateInput) => Promise<void>;
  onUpdateMcp: (input: McpUpdateInput) => Promise<void>;
  onRemoveMcp: (serverId: string) => Promise<void>;
}

export function SettingsPage({
  initialCategory = "model",
  sessionId,
  onOpenMemorySource,
  runtime,
  modelList,
  modelLoading,
  modelError,
  plugins,
  skills,
  mcpServers,
  extensionError,
  pendingAction,
  pendingSkillIds = EMPTY_PENDING_SKILL_IDS,
  hasBlockingModal = false,
  onClose,
  onModelsChanged,
  onImportPlugin,
  onInstallBuiltinPlugin,
  onExtensionsChanged,
  onTogglePlugin,
  onTrustPluginHooks,
  onRemovePlugin,
  onToggleSkill,
  onRemoveSkill,
  onToggleMcp,
  onCreateMcp,
  onUpdateMcp,
  onRemoveMcp,
}: SettingsPageProps) {
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>(initialCategory);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [personalizationDirty, setPersonalizationDirty] = useState(false);
  const [personalizationBusy, setPersonalizationBusy] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<SettingsCategory | "close">();

  const navigate = useCallback((target: SettingsCategory | "close") => {
    if (activeCategory === "personalization") {
      if (personalizationBusy) return;
      if (personalizationDirty) { setPendingNavigation(target); return; }
    }
    if (target === "close") onClose();
    else setActiveCategory(target);
  }, [activeCategory, personalizationBusy, personalizationDirty, onClose]);

  useEffect(() => {
    if (!pendingNavigation || personalizationDirty || personalizationBusy) return;
    const target = pendingNavigation;
    setPendingNavigation(undefined);
    if (target === "close") onClose();
    else setActiveCategory(target);
  }, [pendingNavigation, personalizationDirty, personalizationBusy, onClose]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (
        event.key === "Escape"
        && !hasBlockingModal
        && !document.querySelector("[role='dialog'], [role='alertdialog']")
      ) {
        navigate("close");
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [hasBlockingModal, navigate]);

  function dismissToast(id: string) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  const showToast = useCallback((message: string, type: "success" | "info" | "error" = "info") => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts((prev) => [...prev, { id, type, message, duration: 3500 }]);
  }, []);

  useEffect(() => {
    if (extensionError) {
      showToast(extensionError, "error");
    }
  }, [extensionError, showToast]);

  useEffect(() => {
    if (modelError) {
      showToast(modelError, "error");
    }
  }, [modelError, showToast]);

  const storageReady = runtime.state === "ready" && runtime.storageHealth.state === "ready";
  const standaloneSkills = skills.filter((skill) => skill.sourceKind !== "plugin");
  const standaloneServers = mcpServers.filter((server) => server.pluginId === "manual");

  // Navigation badge counts & warnings
  const mcpHasError = standaloneServers.some((s) => Boolean(s.errorCode));
  const groups: SettingsNavGroup[] = [
    {
      title: "使用偏好",
      items: [
        { id: "personalization", label: "个性化" },
        { id: "memory", label: "记忆" },
      ],
    },
    {
      title: "模型与智能体",
      items: [
        { id: "model", label: "模型" },
        { id: "agents", label: "智能体" },
      ],
    },
    {
      title: "工具与连接",
      items: [
        { id: "web_search", label: "网页搜索" },
        { id: "plugins", label: "插件", count: plugins.length },
        { id: "skills", label: "技能", count: standaloneSkills.length },
        {
          id: "mcp",
          label: "MCP 服务",
          count: standaloneServers.length,
          badgeTone: mcpHasError ? "danger" : undefined,
        },
        { id: "channels", label: "消息渠道" },
      ],
    },
    {
      title: "工作环境",
      items: [
        { id: "worktrees", label: "Git 工作树" },
        { id: "runtime", label: "运行环境" },
      ],
    },
  ];

  return (
    <div className="settings-page-wrapper">
      <header className="settings-page-header">
        <div className="header-left">
          <Button
            variant="secondary"
            size="small"
            onClick={() => navigate("close")}
            disabled={activeCategory === "personalization" && personalizationBusy}
            aria-label="返回 Eidos"
          >
            ← 返回 Eidos
          </Button>
        </div>
        <div className="header-center">
          <span className="settings-header-title">设置</span>
        </div>
        <div className="header-right">
          <span className="version-pill">Eidos {runtime.state === "ready" ? runtime.runtimeVersion : "0.x"}</span>
        </div>
      </header>

      <main className="settings-page-body">
        <div className="settings-container">
          <aside className="settings-sidebar-col">
            <SettingsNavigation
              groups={groups}
              activeCategory={activeCategory}
              onSelectCategory={category => { if (category !== activeCategory) navigate(category); }}
            />
          </aside>

          <section className="settings-content-col">
            {activeCategory === "personalization" && <PersonalizationSettings ready={storageReady} paused={pendingNavigation !== undefined} onShowToast={showToast} onDirtyChange={setPersonalizationDirty} onBusyChange={setPersonalizationBusy} />}
            {activeCategory === "web_search" && (storageReady ? <WebSearchSettings onShowToast={showToast}/> : <p role="alert">Runtime 尚未就绪。</p>)}
            {activeCategory === "channels" && (storageReady ? <ChannelSettings modelList={modelList}/> : <p role="alert">Runtime 尚未就绪。</p>)}
            {activeCategory === "memory" && storageReady && <MemorySettings onOpenSource={onOpenMemorySource} onShowToast={showToast} />}
            {activeCategory === "memory" && !storageReady && <p role="alert">Runtime 尚未就绪。</p>}
            {activeCategory === "model" && (
              <ModelSettings
                modelList={modelList}
                modelLoading={modelLoading}
                modelError={modelError}
                storageHealthReady={storageReady}
                onModelsChanged={onModelsChanged}
                onShowToast={showToast}
              />
            )}

            {activeCategory === "plugins" && (
              <PluginSettings
                sessionId={sessionId}
                plugins={plugins}
                skills={skills}
                mcpServers={mcpServers}
                storageReady={storageReady}
                resourceError={extensionError}
                pendingAction={pendingAction}
                pendingSkillIds={pendingSkillIds}
                onImportPlugin={onImportPlugin}
                onInstallBuiltinPlugin={onInstallBuiltinPlugin}
                onExtensionsChanged={onExtensionsChanged}
                onTogglePlugin={onTogglePlugin}
                onTrustPluginHooks={onTrustPluginHooks}
                onRemovePlugin={onRemovePlugin}
                onToggleSkill={onToggleSkill}
                onToggleMcp={onToggleMcp}
                onShowToast={showToast}
              />
            )}

            {activeCategory === "skills" && (
              <SkillSettings
                skills={standaloneSkills}
                pendingAction={pendingAction}
                pendingSkillIds={pendingSkillIds}
                storageReady={storageReady}
                onToggleSkill={onToggleSkill}
                onRemoveSkill={onRemoveSkill}
                onShowToast={showToast}
              />
            )}

            {activeCategory === "agents" && <AgentSettings ready={storageReady} onShowToast={showToast} />}

            {activeCategory === "mcp" && (
              <McpSettings
                servers={standaloneServers}
                pendingAction={pendingAction}
                onToggleMcp={onToggleMcp}
                onCreateMcp={onCreateMcp}
                onUpdateMcp={onUpdateMcp}
                onRemoveMcp={onRemoveMcp}
                onShowToast={showToast}
              />
            )}

            {activeCategory === "runtime" && (
              <RuntimeSettings runtime={runtime} onShowToast={showToast} />
            )}

            {activeCategory === "worktrees" && <WorktreeSettings onShowToast={showToast} />}
          </section>
        </div>
      </main>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
      <ConfirmDialog open={pendingNavigation !== undefined} title="离开个性化设置？" description="你尚未保存的修改将被丢弃，已经自动保存的设置会保留。" confirmLabel="丢弃并离开" isDestructive busy={personalizationBusy}
        onCancel={() => setPendingNavigation(undefined)} onConfirm={() => {
          const target = pendingNavigation;
          discardPersonalizationDraft();
          setPendingNavigation(undefined); setPersonalizationDirty(false); setPersonalizationBusy(false);
          if (target === "close") onClose();
          else if (target) setActiveCategory(target);
        }} />
    </div>
  );
}

const EMPTY_PENDING_SKILL_IDS: ReadonlySet<string> = new Set();
