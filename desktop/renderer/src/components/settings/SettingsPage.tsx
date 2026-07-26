import React, { useEffect, useState } from "react";
import type {
  ModelListResult,
  ModelStatus,
  McpServerRecord,
  PluginRecord,
  RuntimeStatus,
  SkillMetadata,
} from "../../contracts.js";
import type { SettingsCategory, SettingsPendingAction, SettingsToast } from "./settings-types.js";
import { SettingsCategoryItem, SettingsNavigation } from "./SettingsNavigation.js";
import { ModelSettings } from "./ModelSettings.js";
import { PluginSettings } from "./PluginSettings.js";
import { SkillSettings } from "./SkillSettings.js";
import { McpSettings } from "./McpSettings.js";
import { RuntimeSettings } from "./RuntimeSettings.js";
import { Button } from "../Button.js";

interface SettingsPageProps {
  runtime: RuntimeStatus;
  model?: ModelStatus | undefined;
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
  onConfigureModel: (apiKey: string) => Promise<void>;
  onImportPlugin: () => Promise<void>;
  onTogglePlugin: (pluginId: string, enabled: boolean) => Promise<void>;
  onRemovePlugin: (pluginId: string) => Promise<void>;
  onToggleMcp: (pluginId: string, serverId: string, enabled: boolean) => Promise<void>;
}

export function SettingsPage({
  runtime,
  model,
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
  onConfigureModel,
  onImportPlugin,
  onTogglePlugin,
  onRemovePlugin,
  onToggleMcp,
}: SettingsPageProps) {
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>("model");
  const [toasts, setToasts] = useState<SettingsToast[]>([]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !hasBlockingModal) {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [hasBlockingModal, onClose]);

  function showToast(message: string, type: "success" | "info" | "error" = "info") {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }

  const storageReady = runtime.state === "ready" && runtime.storageHealth.state === "ready";

  // Navigation badge counts & warnings
  const mcpHasError = mcpServers.some((s) => Boolean(s.errorCode));
  const categories: SettingsCategoryItem[] = [
    { id: "model", label: "模型与 API" },
    { id: "plugins", label: "Plugins", count: plugins.length },
    { id: "skills", label: "Skills", count: skills.length },
    {
      id: "mcp",
      label: "MCP Servers",
      count: mcpServers.length,
      badgeTone: mcpHasError ? "danger" : undefined,
    },
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
            {extensionError && (
              <p className="error-banner" role="alert">{extensionError}</p>
            )}

            {activeCategory === "model" && (
              <ModelSettings
                model={model}
                modelList={modelList}
                modelLoading={modelLoading}
                modelError={modelError}
                pendingAction={pendingAction}
                storageHealthReady={storageReady}
                onConfigureModel={onConfigureModel}
                onShowToast={showToast}
              />
            )}

            {activeCategory === "plugins" && (
              <PluginSettings
                plugins={plugins}
                pendingAction={pendingAction}
                onImportPlugin={onImportPlugin}
                onTogglePlugin={onTogglePlugin}
                onRemovePlugin={onRemovePlugin}
                onShowToast={showToast}
              />
            )}

            {activeCategory === "skills" && (
              <SkillSettings skills={skills} />
            )}

            {activeCategory === "mcp" && (
              <McpSettings
                servers={mcpServers}
                pendingAction={pendingAction}
                onToggleMcp={onToggleMcp}
                onShowToast={showToast}
              />
            )}

            {activeCategory === "runtime" && (
              <RuntimeSettings runtime={runtime} model={model} />
            )}
          </section>
        </div>
      </main>

      {toasts.length > 0 && (
        <div className="settings-toast-container" aria-live="polite">
          {toasts.map((toast) => (
            <div key={toast.id} className={`settings-toast settings-toast--${toast.type}`}>
              <span className="toast-icon">
                {toast.type === "success" ? "✓" : toast.type === "error" ? "✕" : "ℹ"}
              </span>
              <span className="toast-message">{toast.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
