import { useEffect, useState } from "react";
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
import { SettingsCategoryItem, SettingsNavigation } from "./SettingsNavigation.js";
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
  hasBlockingModal?: boolean | undefined;
  onClose: () => void;
  onModelsChanged: () => Promise<void>;
  onImportPlugin: () => Promise<PluginRecord | null>;
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
  hasBlockingModal = false,
  onClose,
  onModelsChanged,
  onImportPlugin,
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

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (
        event.key === "Escape"
        && !hasBlockingModal
        && !document.querySelector("[role='dialog'], [role='alertdialog']")
      ) {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [hasBlockingModal, onClose]);

  function dismissToast(id: string) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  function showToast(message: string, type: "success" | "info" | "error" = "info") {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts((prev) => [...prev, { id, type, message, duration: 3500 }]);
  }

  useEffect(() => {
    if (extensionError) {
      showToast(extensionError, "error");
    }
  }, [extensionError]);

  const storageReady = runtime.state === "ready" && runtime.storageHealth.state === "ready";
  const standaloneSkills = skills.filter((skill) => skill.sourceKind !== "plugin");
  const standaloneServers = mcpServers.filter((server) => server.pluginId === "manual");

  // Navigation badge counts & warnings
  const mcpHasError = standaloneServers.some((s) => Boolean(s.errorCode));
  const categories: SettingsCategoryItem[] = [
    { id: "model", label: "模型" },
    { id: "web_search", label: "网页搜索" },
    { id: "channels", label: "助理" },
    { id: "memory", label: "记忆" },
    { id: "plugins", label: "Plugins", count: plugins.length },
    { id: "skills", label: "技能", count: standaloneSkills.length },
    { id: "agents", label: "智能体" },
    {
      id: "mcp",
      label: "MCP Servers",
      count: standaloneServers.length,
      badgeTone: mcpHasError ? "danger" : undefined,
    },
    { id: "worktrees", label: "Worktrees" },
    { id: "runtime", label: "Runtime" },
  ];

  return (
    <div className="settings-page-wrapper">
      <header className="settings-page-header">
        <div className="header-left">
          <Button
            variant="secondary"
            size="small"
            onClick={onClose}
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
              categories={categories}
              activeCategory={activeCategory}
              onSelectCategory={setActiveCategory}
            />
          </aside>

          <section className="settings-content-col">
            {activeCategory === "web_search" && (storageReady ? <WebSearchSettings onShowToast={showToast}/> : <p role="alert">Runtime 尚未就绪。</p>)}
            {activeCategory === "channels" && (storageReady ? <ChannelSettings modelList={modelList}/> : <p role="alert">Runtime 尚未就绪。</p>)}
            {activeCategory === "memory" && storageReady && <MemorySettings sessionId={sessionId} onOpenSource={onOpenMemorySource} onShowToast={showToast} />}
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
                plugins={plugins}
                skills={skills}
                mcpServers={mcpServers}
                storageReady={storageReady}
                resourceError={extensionError}
                pendingAction={pendingAction}
                onImportPlugin={onImportPlugin}
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
    </div>
  );
}
