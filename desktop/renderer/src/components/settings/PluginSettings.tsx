import { useRef, useState } from "react";
import type { McpServerRecord, PluginRecord, SkillMetadata } from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { Button } from "../Button.js";
import { DropdownMenu } from "../DropdownMenu.js";
import { TrashIcon } from "../MenuIcons.js";
import { SettingSection } from "../SettingSection.js";
import { SettingRow } from "../SettingRow.js";
import { Toggle } from "../Toggle.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { EmptyState } from "../EmptyState.js";
import { StatusBadge } from "../StatusBadge.js";
import { AlertBanner } from "../AlertBanner.js";
import { PluginDetailDialog } from "./PluginDetailDialog.js";
import { HookSourceSettings } from "./HookSourceSettings.js";
import { compatibilityIssue } from "./plugin-compatibility.js";
import { BuiltinPlugins } from "./BuiltinPlugins.js";

interface PluginSettingsProps {
  sessionId?: string | undefined;
  plugins: PluginRecord[];
  skills?: SkillMetadata[] | undefined;
  mcpServers?: McpServerRecord[] | undefined;
  storageReady?: boolean | undefined;
  resourceError?: string | undefined;
  onToggleSkill?: ((qualifiedId: string, enabled: boolean) => Promise<void>) | undefined;
  onToggleMcp?: ((pluginId: string, serverId: string, enabled: boolean) => Promise<void>) | undefined;
  pendingAction: SettingsPendingAction;
  onImportPlugin: () => Promise<PluginRecord | null>;
  onInstallBuiltinPlugin?: ((pluginId: string) => Promise<void>) | undefined;
  onExtensionsChanged?: (() => Promise<void>) | undefined;
  onTogglePlugin: (pluginId: string, enabled: boolean) => Promise<void>;
  onTrustPluginHooks?: ((pluginId: string, contentHash: string, trusted: boolean) => Promise<void>) | undefined;
  onRemovePlugin: (pluginId: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}

export function PluginSettings({
  sessionId,
  plugins,
  skills = [],
  mcpServers = [],
  storageReady = true,
  resourceError,
  onToggleSkill,
  onToggleMcp,
  pendingAction,
  onImportPlugin,
  onInstallBuiltinPlugin,
  onExtensionsChanged,
  onTogglePlugin,
  onTrustPluginHooks,
  onRemovePlugin,
  onShowToast,
}: PluginSettingsProps) {
  const [pluginToRemove, setPluginToRemove] = useState<PluginRecord | null>(null);
  const [localError, setLocalError] = useState<string>();
  const [review, setReview] = useState<{ plugin: PluginRecord; enable: boolean } | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const importRef = useRef<HTMLButtonElement>(null);
  const selected = plugins.find((plugin) => plugin.id === selectedId);

  const isImporting = pendingAction?.type === "import_plugin";
  const enabledCount = plugins.filter((p) => p.enabled).length;

  async function handleImport() {
    setLocalError(undefined);
    try {
      const imported = await onImportPlugin();
      if (imported) {
        const previous = plugins.find(plugin => plugin.id === imported.id);
        const updated = previous && previous.version !== imported.version;
        onShowToast(updated ? `Plugin 已更新至 v${imported.version}，请重新启用并审阅授权。` : "Plugin 导入成功", "success");
      }
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "导入 Plugin 失败";
      onShowToast(msg, "error");
    }
  }

  async function handleToggle(plugin: PluginRecord, nextState: boolean) {
    setLocalError(undefined);
    if (nextState && plugin.hookReview && (plugin.hookReview.executableCount ?? 0) > 0 && !plugin.hookReview.trusted) {
      setReview({ plugin, enable: true });
      return;
    }
    try {
      await onTogglePlugin(plugin.id, nextState);
      onShowToast(
        `Plugin “${plugin.name}” 已${nextState ? "启用" : "停用"}`,
        "info"
      );
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "更新 Plugin 状态失败";
      onShowToast(msg, "error");
    }
  }

  async function trustHooks() {
    if (!review || !onTrustPluginHooks || !review.plugin.hookReview) return;
    setReviewBusy(true);
    setLocalError(undefined);
    try {
      await onTrustPluginHooks(review.plugin.id, review.plugin.hookReview.contentHash, true);
      if (review.enable) await onTogglePlugin(review.plugin.id, true);
      setReview(null);
      onShowToast("Hook 已信任，将在后续任务中自动运行。", "success");
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "保存 Hook 信任失败";
      setLocalError(msg);
      onShowToast(msg, "error");
    } finally {
      setReviewBusy(false);
    }
  }

  async function revokeHooks(plugin: PluginRecord) {
    if (!onTrustPluginHooks) return;
    try {
      await onTrustPluginHooks(plugin.id, plugin.contentHash, false);
      onShowToast("Hook 信任已撤销，后续调用将跳过。", "info");
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "撤销 Hook 信任失败";
      onShowToast(msg, "error");
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
      onShowToast(msg, "error");
    }
  }

  return (
    <div className="settings-panel plugin-settings">
      <div className="plugin-settings-content" inert={Boolean(selected || review || pluginToRemove)}>
      <div className="settings-panel-header">
        <div className="header-title-with-stats">
          <div>
            <h1>Plugins 扩展</h1>
            <p className="settings-panel-subtitle">
              导入本地 Eidos 或 Codex 插件，也可以导入新版本完成更新。Hook 和 MCP 服务分别审阅授权。
            </p>
          </div>
          <Button
            ref={importRef}
            variant="primary"
            size="medium"
            disabled={isImporting}
            loading={isImporting}
            onClick={() => void handleImport()}
          >
            导入本地 Plugin
          </Button>
        </div>
      </div>

      <div className="plugin-stats-summary">
        <div className="stat-badge">已导入 {plugins.length} 项</div>
        <div className="stat-badge stat-badge--active">已启用 {enabledCount} 项</div>
      </div>

      {onInstallBuiltinPlugin && <BuiltinPlugins onInstall={onInstallBuiltinPlugin} onShowToast={onShowToast}
        catalogRevision={plugins.map(plugin => `${plugin.id}:${plugin.version}:${plugin.contentHash}`).join("|")} />}
      {storageReady && <HookSourceSettings sessionId={sessionId} />}
      <SettingSection title="已安装的 Plugins">
        {plugins.length === 0 ? (
          <EmptyState
            title="尚未导入任何 Plugin"
            description="导入 Plugin 后，您可以点击插件名称，查看和管理它的资源。"
            action={
              <Button
                variant="secondary"
                size="medium"
                disabled={isImporting}
                loading={isImporting}
                onClick={() => void handleImport()}
              >
                导入本地 Plugin
              </Button>
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
                    {plugin.presentation?.icon && <img className="plugin-icon" src={plugin.presentation.icon} alt="" />}
                    <button type="button" className="plugin-name plugin-name-button" aria-label={`查看 ${plugin.name} 的资源`} disabled={isRowPending} onClick={() => setSelectedId(plugin.id)}>{plugin.name}</button>
                    <span className="plugin-version">{plugin.compatibility && !plugin.compatibility.declaredVersion ? "未声明版本" : `v${plugin.version}`}</span>
                    {plugin.enabled ? (
                      <StatusBadge tone="success" dot>已启用</StatusBadge>
                    ) : (
                      <StatusBadge tone="neutral" dot={false}>已停用</StatusBadge>
                    )}
                  </div>
                }
                description={<>
                  <span>{plugin.description || "未提供描述"}</span>
                  {plugin.hookReview && (plugin.hookReview.definitions?.length ?? 0) > 0 && <div className="plugin-hook-status">
                    Hook：{plugin.hookReview.trusted ? "已信任" : "等待审阅"} · {(plugin.hookReview.definitions?.length ?? 0)} 项
                    {plugin.hookReview.latest && <span> · 最近执行：{(plugin.hookReview.latest.status ?? "completed") === "completed" ? "已完成" : "未完成"}{plugin.hookReview.latest.errorCode ? `（${plugin.hookReview.latest.errorCode}）` : ""}</span>}
                  </div>}
                  {Boolean(plugin.compatibility?.issues?.length) && <div className="plugin-hook-status">此插件包含当前版本暂不支持的能力，请查看兼容信息。</div>}
                </>}
                meta={
                  <div className="plugin-meta-info">
                    <span>{plugin.presentation?.developerName || plugin.id}</span>
                    <span className="meta-divider">·</span>
                    <span>{skills.filter(skill => skill.pluginId === plugin.id).length} 个技能</span>
                    <span className="meta-divider">·</span>
                    <span>{mcpServers.filter(server => server.pluginId === plugin.id).length} 个 MCP 服务</span>
                  </div>
                }
                action={
                  <div className="plugin-actions-group">
                    {plugin.hookReview && (plugin.hookReview.definitions?.length ?? 0) > 0 && <Button variant="secondary" size="small" disabled={isRowPending} onClick={() => setReview({ plugin, enable: false })}>审阅 Hook</Button>}
                    <Toggle
                      checked={plugin.enabled}
                      disabled={isRowPending}
                      label={`启用或停用 ${plugin.name}`}
                      onChange={(next) => void handleToggle(plugin, next)}
                    />
                    <DropdownMenu
                      trigger="⋯"
                      label={`${plugin.name} 选项`}
                      items={[
                        ...(plugin.hookReview?.trusted ? [{ key: "revoke-hooks", label: "撤销 Hook 信任", disabled: isRowPending, onClick: () => void revokeHooks(plugin) }] : []),
                        {
                          key: "remove",
                          label: "移除 Plugin",
                          icon: <TrashIcon />,
                          danger: true,
                          disabled: isRowPending,
                          onClick: () => setPluginToRemove(plugin),
                        },
                      ]}
                    />
                  </div>
                }
              />
            );
          })
        )}
      </SettingSection>
      </div>

      <PluginDetailDialog
        plugin={selected}
        skills={skills}
        servers={mcpServers}
        pendingAction={pendingAction}
        storageReady={storageReady}
        resourceError={resourceError}
        obscured={Boolean(review)}
        onToggleSkill={onToggleSkill}
        onToggleMcp={onToggleMcp}
        onExtensionsChanged={onExtensionsChanged}
        onReviewHooks={() => { if (selected) setReview({ plugin: selected, enable: false }); }}
        onShowToast={onShowToast}
        onClose={() => setSelectedId(undefined)}
        getFallbackFocus={() => importRef.current}
      />

      <ConfirmDialog
        open={Boolean(review)} title={`审阅 ${review?.plugin.name ?? ""} 的 Hook`}
        description={<div className="plugin-hook-review">
          <p>您信任后，插件会在匹配的任务阶段自动执行以下命令。</p>
          <p>脚本使用当前任务的工作区和网络权限。脚本还可以读取本插件的安装目录，并写入本插件的私有数据目录。完全访问任务使用当前 macOS 用户的权限。</p>
          <p>安装目录：<code>{review?.plugin.hookReview?.installedRoot}</code><br />可写数据目录：<code>{review?.plugin.hookReview?.dataDirectory}</code></p>
          {review?.plugin.hookReview?.definitions?.map((hook) => <div key={hook.key}>
            <strong>{hook.event}{hook.matcher ? ` · ${hook.matcher}` : ""}</strong>
            <pre className="plugin-hook-command">{hook.handlerType === "mcp_tool" ? JSON.stringify({ server: hook.mcpServer, tool: hook.mcpTool, input: hook.mcpInput }, null, 2) : hook.command || `处理器：${hook.handlerType}`}</pre>
          </div>)}
          {Boolean(review?.plugin.compatibility?.issues?.length) && <div><p>以下能力暂不支持：</p><ul>{review?.plugin.compatibility?.issues?.map((issue) => <li key={issue}>{compatibilityIssue(issue)}</li>)}</ul></div>}
          {review?.plugin.hookReview?.latest && <p>最近结果：{review.plugin.hookReview.latest.systemMessage || review.plugin.hookReview.latest.message || review.plugin.hookReview.latest.errorCode || "脚本已完成。"}</p>}
          {localError && <AlertBanner tone="error">{localError}</AlertBanner>}
        </div>}
        confirmLabel={review?.plugin.hookReview?.trusted || !(review?.plugin.hookReview?.executableCount) ? "关闭" : "信任此版本的 Hook"}
        cancelLabel="取消" isDestructive={Boolean(review && !review.plugin.hookReview?.trusted && review.plugin.hookReview?.executableCount)} busy={reviewBusy}
        onConfirm={() => review?.plugin.hookReview?.trusted || !(review?.plugin.hookReview?.executableCount) ? setReview(null) : void trustHooks()}
        onCancel={() => { if (!reviewBusy) setReview(null); }}
      />

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
