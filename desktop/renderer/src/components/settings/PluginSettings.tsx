import React, { useState } from "react";
import type { PluginRecord } from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { SettingSection } from "./SettingSection";
import { SettingRow } from "./SettingRow";
import { Toggle } from "./Toggle";
import { ConfirmDialog } from "./ConfirmDialog";
import { EmptySettingsState } from "./EmptySettingsState";
import { StatusBadge } from "./StatusBadge";

interface PluginSettingsProps {
  plugins: PluginRecord[];
  pendingAction: SettingsPendingAction;
  onImportPlugin: () => Promise<void>;
  onTogglePlugin: (pluginId: string, enabled: boolean) => Promise<void>;
  onRemovePlugin: (pluginId: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}

export function PluginSettings({
  plugins,
  pendingAction,
  onImportPlugin,
  onTogglePlugin,
  onRemovePlugin,
  onShowToast,
}: PluginSettingsProps) {
  const [pluginToRemove, setPluginToRemove] = useState<PluginRecord | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string>();

  const isImporting = pendingAction?.type === "import_plugin";
  const enabledCount = plugins.filter((p) => p.enabled).length;

  async function handleImport() {
    setLocalError(undefined);
    try {
      await onImportPlugin();
      onShowToast("Plugin 导入成功", "success");
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "导入 Plugin 失败";
      setLocalError(msg);
    }
  }

  async function handleToggle(plugin: PluginRecord, nextState: boolean) {
    setLocalError(undefined);
    try {
      await onTogglePlugin(plugin.id, nextState);
      onShowToast(
        `Plugin “${plugin.name}” 已${nextState ? "启用" : "停用"}`,
        "info"
      );
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "更新 Plugin 状态失败";
      setLocalError(msg);
    }
  }

  async function confirmRemove() {
    if (!pluginToRemove) return;
    setLocalError(undefined);
    try {
      await onRemovePlugin(pluginToRemove.id);
      onShowToast(`Plugin “${pluginToRemove.name}” 已移除`, "info");
      setPluginToRemove(null);
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "移除 Plugin 失败";
      setLocalError(msg);
    }
  }

  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <div className="header-title-with-stats">
          <div>
            <h1>Plugins 扩展</h1>
            <p className="settings-panel-subtitle">
              导入并管理本地配置文件包。只读取配置，不自动执行第三方安装脚本。
            </p>
          </div>
          <button
            type="button"
            className="button-primary"
            disabled={isImporting}
            onClick={() => void handleImport()}
          >
            {isImporting ? "导入中…" : "导入本地 Plugin"}
          </button>
        </div>
      </div>

      {localError && <p className="setting-banner-error" role="alert">{localError}</p>}

      <div className="plugin-stats-summary">
        <div className="stat-badge">已导入 {plugins.length} 项</div>
        <div className="stat-badge stat-badge--active">已启用 {enabledCount} 项</div>
      </div>

      <SettingSection title="已安装的 Plugins">
        {plugins.length === 0 ? (
          <EmptySettingsState
            title="尚未导入任何 Plugin"
            description="导入 Plugin 后，相关的 Skills 能力和 MCP Servers 将显示在设置列表中。"
            action={
              <button
                type="button"
                className="button-secondary"
                disabled={isImporting}
                onClick={() => void handleImport()}
              >
                导入本地 Plugin
              </button>
            }
          />
        ) : (
          plugins.map((plugin) => {
            const isToggling =
              pendingAction?.type === "toggle_plugin" &&
              pendingAction.pluginId === plugin.id;
            const isRemoving =
              pendingAction?.type === "remove_plugin" &&
              pendingAction.pluginId === plugin.id;
            const isRowPending = isToggling || isRemoving;

            return (
              <SettingRow
                key={plugin.id}
                disabled={isRowPending}
                title={
                  <div className="plugin-row-header">
                    <span className="plugin-name">{plugin.name}</span>
                    <span className="plugin-version">v{plugin.version}</span>
                    {plugin.enabled ? (
                      <StatusBadge tone="success" dot>已启用</StatusBadge>
                    ) : (
                      <StatusBadge tone="neutral" dot={false}>已停用</StatusBadge>
                    )}
                  </div>
                }
                description={plugin.description || "未提供描述"}
                meta={
                  <div className="plugin-meta-info">
                    <code>{plugin.id}</code>
                    <span className="meta-divider">•</span>
                    <code>{plugin.contentHash.slice(0, 10)}</code>
                  </div>
                }
                action={
                  <div className="plugin-actions-group">
                    <Toggle
                      checked={plugin.enabled}
                      disabled={isRowPending}
                      label={`启用或停用 ${plugin.name}`}
                      onChange={(next) => void handleToggle(plugin, next)}
                    />
                    <div className="menu-dropdown-wrapper">
                      <button
                        type="button"
                        className="icon-button"
                        aria-label="Plugin 更多选项"
                        disabled={isRowPending}
                        onClick={() =>
                          setOpenMenuId(openMenuId === plugin.id ? null : plugin.id)
                        }
                      >
                        ⋯
                      </button>
                      {openMenuId === plugin.id && (
                        <div className="dropdown-menu" role="menu">
                          <button
                            type="button"
                            role="menuitem"
                            className="danger-action"
                            onClick={() => {
                              setOpenMenuId(null);
                              setPluginToRemove(plugin);
                            }}
                          >
                            移除 Plugin
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                }
              />
            );
          })
        )}
      </SettingSection>

      <ConfirmDialog
        open={Boolean(pluginToRemove)}
        title={`移除 Plugin “${pluginToRemove?.name ?? ""}”？`}
        description={
          <div>
            <p>确定要从 Eidos 中移除此 Plugin 吗？</p>
            <p className="dialog-note">
              注意：移除后相关 Skill 与 MCP Server 将失效，但历史 Task / Run 的来源记录会被保留。
            </p>
          </div>
        }
        confirmLabel="移除"
        cancelLabel="取消"
        isDestructive
        busy={pendingAction?.type === "remove_plugin"}
        onConfirm={() => void confirmRemove()}
        onCancel={() => setPluginToRemove(null)}
      />
    </div>
  );
}
