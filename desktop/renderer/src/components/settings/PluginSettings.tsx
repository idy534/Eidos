import { useRef, useState } from "react";
import type { McpServerRecord, PluginRecord, SkillMetadata } from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { Button } from "../Button.js";
import { DropdownMenu, type DropdownMenuItem } from "../DropdownMenu.js";
import { FinderIcon, TrashIcon } from "../MenuIcons.js";
import { SettingSection } from "../SettingSection.js";
import { SettingRow } from "../SettingRow.js";
import { Toggle } from "../Toggle.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { EmptyState } from "../EmptyState.js";
import { StatusBadge } from "../StatusBadge.js";
import { PluginDetailDialog } from "./PluginDetailDialog.js";
import { HookSourceSettings } from "./HookSourceSettings.js";
import { compatibilityIssue } from "./plugin-compatibility.js";
import { PluginMarketplaceDialog } from "./PluginMarketplaceDialog.js";
import { PluginsSettingsIcon, githubIcon } from "./SettingsIcons.js";
import { ChevronDownIcon } from "./MemoryIcons.js";

interface PluginSettingsProps {
  sessionId?: string | undefined;
  plugins: PluginRecord[];
  skills?: SkillMetadata[] | undefined;
  mcpServers?: McpServerRecord[] | undefined;
  storageReady?: boolean | undefined;
  resourceError?: string | undefined;
  pendingSkillIds?: ReadonlySet<string> | undefined;
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
  pendingSkillIds,
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
  const [review, setReview] = useState<{ plugin: PluginRecord; enable: boolean } | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string>();
  const [selectedId, setSelectedId] = useState<string>();
  const [marketplaceOpen, setMarketplaceOpen] = useState(false);
  const importRef = useRef<HTMLButtonElement>(null);
  const selected = plugins.find((plugin) => plugin.id === selectedId);

  const isImporting = pendingAction?.type === "import_plugin";
  const enabledCount = plugins.filter((p) => p.enabled).length;

  const importMenuItems: DropdownMenuItem[] = [
    {
      key: "import-local",
      label: "导入本地 Plugin",
      icon: <FinderIcon width={14} height={14} />,
      disabled: isImporting,
      onClick: () => void handleImport(),
    },
  ];

  async function handleImport() {
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
    setReviewError(undefined);
    try {
      await onTrustPluginHooks(review.plugin.id, review.plugin.hookReview.contentHash, true);
      if (review.enable) await onTogglePlugin(review.plugin.id, true);
      setReview(null);
      onShowToast("Hook 已信任，将在后续任务中自动运行。", "success");
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "保存 Hook 信任失败";
      setReviewError(msg);
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
      <div className="plugin-settings-content" inert={Boolean(selected || review || pluginToRemove || marketplaceOpen)}>
      <div className="settings-panel-header">
        <div className="header-title-with-stats">
          <div>
            <h1>Plugins 扩展</h1>
            <p className="settings-panel-subtitle">
              导入本地Eidos插件(兼容Codex)，也可以导入新版本完成更新。
            </p>
          </div>
          <div className="plugin-header-actions">
            <div className="split-button-group">
              <Button
                ref={importRef}
                size="medium"
                className="split-button-main"
                icon={<PluginsSettingsIcon width={14} height={14} className="split-button-icon" />}
                disabled={isImporting}
                loading={isImporting}
                onClick={() => setMarketplaceOpen(true)}
              >
                插件市场
              </Button>
              <DropdownMenu
                className="split-button-dropdown"
                triggerClassName="split-button-caret"
                triggerAriaLabel="更多插件选项"
                align="right"
                trigger={<ChevronDownIcon width={13} height={13} className="split-button-caret-icon" />}
                items={importMenuItems}
              />
            </div>
          </div>
        </div>
      </div>

      <div className="plugin-stats-summary">
        <div className="stat-badge">已导入 {plugins.length} 项</div>
        <div className="stat-badge stat-badge--active">已启用 {enabledCount} 项</div>
      </div>

      <SettingSection title="已安装的 Plugins">
        {plugins.length === 0 ? (
          <EmptyState
            title="尚未导入任何 Plugin"
            description="您可以在插件市场查看随应用提供的官方内置插件，也可以导入本地开发的插件包。"
            action={
              <div className="empty-state-actions">
                <Button
                  variant="primary"
                  size="medium"
                  onClick={() => setMarketplaceOpen(true)}
                >
                  浏览插件市场
                </Button>
                <Button
                  variant="secondary"
                  size="medium"
                  disabled={isImporting}
                  loading={isImporting}
                  onClick={() => void handleImport()}
                >
                  导入本地 Plugin
                </Button>
              </div>
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
            const isGitHub = plugin.id === "eidos-github" || plugin.name.toLowerCase() === "github";
            const iconSrc = plugin.presentation?.icon || (isGitHub ? githubIcon : undefined);

            return (
              <SettingRow
                key={plugin.id}
                disabled={isRowPending}
                title={
                  <div className="plugin-row-header">
                    {iconSrc && <img className="plugin-icon" src={iconSrc} alt={plugin.name} />}
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
      {storageReady && <HookSourceSettings sessionId={sessionId} />}
      </div>

      <PluginMarketplaceDialog
        open={marketplaceOpen}
        onClose={() => setMarketplaceOpen(false)}
        onInstall={onInstallBuiltinPlugin}
        onImportLocal={() => void handleImport()}
        onShowToast={onShowToast}
        catalogRevision={plugins.map(plugin => `${plugin.id}:${plugin.version}:${plugin.contentHash}`).join("|")}
        getFallbackFocus={() => importRef.current}
      />

      <PluginDetailDialog
        plugin={selected}
        skills={skills}
        servers={mcpServers}
        pendingAction={pendingAction}
        pendingSkillIds={pendingSkillIds}
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
        </div>}
        confirmLabel={review?.plugin.hookReview?.trusted || !(review?.plugin.hookReview?.executableCount) ? "关闭" : "信任此版本的 Hook"}
        cancelLabel="取消" isDestructive={Boolean(review && !review.plugin.hookReview?.trusted && review.plugin.hookReview?.executableCount)} busy={reviewBusy}
        error={reviewError}
        onConfirm={() => review?.plugin.hookReview?.trusted || !(review?.plugin.hookReview?.executableCount) ? setReview(null) : void trustHooks()}
        onCancel={() => { if (!reviewBusy) { setReview(null); setReviewError(undefined); } }}
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
