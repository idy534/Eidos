import { useEffect, useRef, useState } from "react";
import type { McpServerRecord, PluginRecord, SkillMetadata } from "../../contracts.js";
import { userFacingError } from "../../session-state.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { StatusBadge } from "../StatusBadge.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { McpDetailDialog } from "./McpDetailDialog.js";
import { SkillDetailDialog, SkillIcon } from "./SkillDetailDialog.js";
import { McpSettingsIcon, SkillsSettingsIcon, githubIcon } from "./SettingsIcons.js";
import { compatibilityIssue } from "./plugin-compatibility.js";
import type { SettingsPendingAction } from "./settings-types.js";
import "./PluginDetailDialog.css";
import { McpConnectionPanel } from "./McpConnectionPanel.js";
import { HookHistoryDialog } from "./HookHistoryDialog.js";

interface PluginDetailDialogProps {
  plugin: PluginRecord | undefined;
  skills: SkillMetadata[];
  servers: McpServerRecord[];
  pendingAction: SettingsPendingAction;
  storageReady: boolean;
  resourceError?: string | undefined;
  obscured: boolean;
  onToggleSkill?: ((id: string, enabled: boolean) => Promise<void>) | undefined;
  onToggleMcp?: ((pluginId: string, serverId: string, enabled: boolean) => Promise<void>) | undefined;
  onExtensionsChanged?: (() => Promise<void>) | undefined;
  onReviewHooks: () => void;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
  onClose: () => void;
  getFallbackFocus: () => HTMLElement | null;
}

export function PluginDetailDialog({
  plugin,
  skills,
  servers,
  pendingAction,
  storageReady,
  resourceError,
  obscured,
  onToggleSkill,
  onToggleMcp,
  onExtensionsChanged,
  onReviewHooks,
  onShowToast,
  onClose,
  getFallbackFocus,
}: PluginDetailDialogProps) {
  const [skillId, setSkillId] = useState<string>();
  const [serverId, setServerId] = useState<string>();
  const [reviewId, setReviewId] = useState<string>();
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [hookHistory, setHookHistory] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const pluginSkills = skills.filter((skill) => skill.sourceKind === "plugin" && skill.pluginId === plugin?.id);
  const pluginServers = servers.filter((server) => server.pluginId === plugin?.id);
  const selectedSkill = pluginSkills.find((skill) => skill.qualifiedId === skillId);
  const selectedServer = pluginServers.find((server) => server.serverId === serverId) ?? null;
  const reviewing = pluginServers.find((server) => server.serverId === reviewId);
  const nested = Boolean(selectedSkill || selectedServer || reviewing || obscured || hookHistory);
  const busy = saving || Boolean(pendingAction);
  const isGitHub = plugin?.id === "eidos-github" || plugin?.name.toLowerCase() === "github";
  const iconSrc = plugin?.presentation?.icon || (isGitHub ? githubIcon : undefined);
  useDialogFocusLifecycle({ open: Boolean(plugin), initialFocusRef: closeRef, getFallbackFocus });

  useEffect(() => {
    setSkillId(undefined);
    setServerId(undefined);
    setReviewId(undefined);
    setError(undefined);
    setHookHistory(false);
  }, [plugin?.id, plugin?.contentHash]);

  useEffect(() => {
    if (!plugin || nested) return;
    function keydown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busy) onClose();
      }
      if (event.key !== "Tab") return;
      const targets = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]), input:not([disabled]), summary, [tabindex='0']"
        ) ?? []
      ).filter(
        (element) =>
          (element.checkVisibility?.({ visibilityProperty: true }) ?? true) &&
          (!element.closest("details:not([open])") || element.tagName === "SUMMARY")
      );
      const first = targets?.[0];
      const last = targets?.[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [plugin, nested, busy, onClose]);

  async function changeMcp(server: McpServerRecord, enabled: boolean) {
    if (!onToggleMcp || busy) return;
    setSaving(true);
    setError(undefined);
    try {
      await onToggleMcp(server.pluginId, server.serverId, enabled);
      setReviewId(undefined);
      onShowToast(enabled ? "MCP 服务已授权并启用" : "MCP 服务已停用", "info");
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setSaving(false);
    }
  }

  if (!plugin) return null;
  const hooks = plugin.hookReview?.definitions ?? [];
  const issues = plugin.compatibility?.issues ?? [];

  return (
    <>
      <div
        className="modal-backdrop"
        inert={nested}
        aria-hidden={nested || undefined}
        onClick={() => {
          if (!busy) onClose();
        }}
      >
        <div
          className="modal-dialog plugin-resource-dialog"
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="plugin-resource-title"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="plugin-resource-header">
            <div className="plugin-resource-header-main">
              <div className="plugin-resource-icon-wrap" aria-hidden="true">
                {iconSrc ? (
                  <img className="plugin-resource-icon-img" src={iconSrc} alt={plugin.name} />
                ) : (
                  <span className="plugin-resource-avatar">
                    {Array.from(plugin.name.trim())[0]?.toLocaleUpperCase() ?? "P"}
                  </span>
                )}
              </div>
              <div className="plugin-resource-title-group">
                <div className="plugin-resource-title-row">
                  <h2 id="plugin-resource-title">{plugin.name}</h2>
                  <StatusBadge tone={plugin.enabled ? "success" : "neutral"} dot>
                    {plugin.enabled ? "已启用" : "已停用"}
                  </StatusBadge>
                  <span className="plugin-version-pill">
                    {plugin.compatibility && !plugin.compatibility.declaredVersion
                      ? "未声明版本"
                      : `v${plugin.version}`}
                  </span>
                </div>
                <p className="plugin-resource-subtitle">
                  {plugin.description || "插件未提供说明。"}
                </p>
              </div>
            </div>
            <button
              ref={closeRef}
              type="button"
              className="skill-detail-close-btn plugin-detail-close-btn"
              aria-label="关闭插件详情"
              disabled={busy}
              onClick={onClose}
            >
              ×
            </button>
          </header>

          <div className="plugin-resource-body" tabIndex={0} aria-label="插件资源">
            {!plugin.enabled && (
              <div className="plugin-disabled-notice" role="status">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="plugin-notice-icon" aria-hidden="true">
                  <circle cx="8" cy="8" r="6.5" />
                  <path d="M8 5v3.5M8 11.5h.01" />
                </svg>
                <span>插件已停用。启用插件后，已开启并获得授权的资源才会生效。</span>
              </div>
            )}

            {resourceError && <AlertBanner tone="error">{resourceError}</AlertBanner>}
            {error && !reviewing && <AlertBanner tone="error">{error}</AlertBanner>}

            <section className="plugin-resource-section" aria-label="插件技能">
              <div className="plugin-section-header">
                <div className="plugin-section-title-wrap">
                  <SkillsSettingsIcon className="plugin-section-icon" width={16} height={16} />
                  <h3>Skills</h3>
                  <span className="plugin-section-count">{pluginSkills.length}</span>
                </div>
              </div>
              <div className="plugin-cards-list">
                {pluginSkills.map((skill) => (
                  <button
                    key={skill.qualifiedId}
                    type="button"
                    className="plugin-resource-item plugin-resource-link plugin-skill-item"
                    onClick={() => setSkillId(skill.qualifiedId)}
                  >
                    <div className="plugin-skill-item-left">
                      <SkillIcon name={skill.name} icon={skill.icon} />
                      <div className="plugin-skill-item-text">
                        <strong className="plugin-skill-title">{skill.name}</strong>
                        <span className="plugin-resource-description">{skill.description || "未提供技能说明"}</span>
                      </div>
                    </div>
                    <div className="plugin-skill-item-right">
                      <span className={`plugin-resource-state ${skill.enabled ? "plugin-state--enabled" : "plugin-state--disabled"}`}>
                        {skill.enabled ? "已开启" : "已关闭"}
                      </span>
                      <span className="plugin-chevron" aria-hidden="true">
                        ›
                      </span>
                    </div>
                  </button>
                ))}
                {!pluginSkills.length && !resourceError && (
                  <div className="plugin-resource-empty-box">
                    <p className="plugin-resource-empty">插件未提供 Skill。</p>
                  </div>
                )}
              </div>
            </section>

            <section className="plugin-resource-section" aria-label="插件 MCP 服务">
              <div className="plugin-section-header">
                <div className="plugin-section-title-wrap">
                  <McpSettingsIcon className="plugin-section-icon" width={16} height={16} />
                  <h3>MCP Servers</h3>
                  <span className="plugin-section-count">{pluginServers.length}</span>
                </div>
              </div>
              <div className="plugin-cards-list">
                {pluginServers.map((server) => {
                  const isGitHubServer =
                    server.serverId === "github" ||
                    server.pluginId === "eidos-github" ||
                    server.remote?.authType === "github-device";
                  const isAvailable = server.available && server.declaredEnabled;
                  const statusClass = server.errorCode
                    ? "plugin-state--danger"
                    : isAvailable
                    ? "plugin-state--enabled"
                    : server.consented
                    ? "plugin-state--warning"
                    : "plugin-state--disabled";
                  const statusLabel = server.errorCode
                    ? "服务异常"
                    : !server.declaredEnabled
                    ? "未开放"
                    : server.available
                    ? "可用"
                    : server.consented
                    ? "已授权，暂不可用"
                    : "等待授权";
                  const serverDescription = server.errorCode
                    ? server.errorCode
                    : isGitHubServer
                    ? "提供 GitHub 仓库、Issue 和 Pull Request 等操作工具"
                    : server.remote
                    ? (server.remote.url ? `远程 MCP 服务 · ${server.remote.url}` : "远程 MCP 工具服务")
                    : (server.executable ? `本地服务 · ${[server.executable, ...server.argv].filter(Boolean).join(" ")}` : "本地 MCP 工具服务");
                  return (
                    <div key={server.serverId} className="plugin-card-item plugin-mcp-item">
                      <button
                        type="button"
                        className="plugin-resource-link plugin-resource-server"
                        aria-label={`查看 ${server.serverId} 的工具`}
                        onClick={() => setServerId(server.serverId)}
                      >
                        <div className="plugin-server-item-left">
                          <div className="plugin-server-icon-badge" aria-hidden="true">
                            {isGitHubServer ? (
                              <img className="plugin-server-icon-img" src={githubIcon} alt="GitHub" />
                            ) : (
                              <McpSettingsIcon width={18} height={18} />
                            )}
                          </div>
                          <div className="plugin-server-text">
                            <strong className="plugin-server-title">{server.serverId}</strong>
                            <span className="plugin-resource-description">
                              {serverDescription}
                            </span>
                          </div>
                        </div>
                        <div className="plugin-server-item-right">
                          <span className={`plugin-resource-state ${statusClass}`}>
                            {statusLabel}
                          </span>
                          <span className="plugin-chevron" aria-hidden="true">
                            ›
                          </span>
                        </div>
                      </button>
                      {!server.remote && (
                        <div className="plugin-mcp-action-wrap">
                          <Button
                            variant={server.consented ? "secondary" : "primary"}
                            size="small"
                            disabled={
                              busy ||
                              !storageReady ||
                              !onToggleMcp ||
                              (!server.consented && (!plugin.enabled || !server.declaredEnabled))
                            }
                            onClick={() => {
                              setError(undefined);
                              if (server.consented) void changeMcp(server, false);
                              else setReviewId(server.serverId);
                            }}
                          >
                            {server.consented ? "停用" : "启用"}
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })}
                {pluginServers
                  .filter((server) => server.remote)
                  .map((server) => (
                    <McpConnectionPanel
                      key={server.serverId}
                      server={server}
                      enabled={plugin.enabled && storageReady}
                      onChanged={onExtensionsChanged}
                    />
                  ))}
                {!pluginServers.length && !resourceError && (
                  <div className="plugin-resource-empty-box">
                    <p className="plugin-resource-empty">插件没有已接入的 MCP 服务。其他声明可在兼容信息中查看。</p>
                  </div>
                )}
              </div>
            </section>

            <section className="plugin-resource-section" aria-label="插件 Hook">
              <div className="plugin-section-header plugin-resource-section-heading">
                <div className="plugin-section-title-wrap">
                  <svg className="plugin-section-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                    <path d="M12 2v4a3 3 0 0 1-3 3H4M4 9l3-3M4 9l3 3" />
                  </svg>
                  <h3>Hooks</h3>
                  <span className="plugin-section-count">{hooks.length}</span>
                </div>
                {hooks.length > 0 && (
                  <div className="plugin-section-actions">
                    <Button variant="secondary" size="small" disabled={busy} onClick={onReviewHooks}>
                      审阅 Hook
                    </Button>
                    <Button variant="secondary" size="small" onClick={() => setHookHistory(true)}>
                      执行记录
                    </Button>
                  </div>
                )}
              </div>

              {hooks.length > 0 && (
                <div className={`plugin-hook-trust-banner ${plugin.hookReview?.trusted ? "trust--ok" : "trust--warn"}`}>
                  <span className="hook-status-dot" aria-hidden="true" />
                  <p>{plugin.hookReview?.trusted ? "此版本的 Hook 已信任。" : "Hook 需要用户审阅并信任后才会执行。"}</p>
                </div>
              )}

              <div className="plugin-cards-list">
                {hooks.map((hook) => (
                  <details className="plugin-resource-hook" key={hook.key}>
                    <summary className="plugin-hook-summary">
                      <span className="plugin-hook-summary-chevron" aria-hidden="true">
                        ›
                      </span>
                      <span className="plugin-hook-tags">
                        <span className="plugin-hook-event-tag">
                          {hook.event}
                          {hook.matcher ? ` · ${hook.matcher}` : ""} · {hook.handlerType}
                          {hook.background ? " · 后台" : ""}
                        </span>
                      </span>
                    </summary>
                    <div className="plugin-hook-body">
                      <pre className="plugin-hook-command">
                        {hook.handlerType === "mcp_tool"
                          ? JSON.stringify({ server: hook.mcpServer, tool: hook.mcpTool, input: hook.mcpInput }, null, 2)
                          : hook.command || `处理器：${hook.handlerType}`}
                      </pre>
                    </div>
                  </details>
                ))}
                {!hooks.length && !resourceError && (
                  <div className="plugin-resource-empty-box">
                    <p className="plugin-resource-empty">插件未提供 Hook。</p>
                  </div>
                )}
              </div>
            </section>

            {issues.length > 0 && (
              <section className="plugin-resource-section" aria-label="插件兼容信息">
                <div className="plugin-section-header">
                  <div className="plugin-section-title-wrap">
                    <svg className="plugin-section-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                      <circle cx="8" cy="8" r="6.5" />
                      <path d="M8 5v3.5M8 11.5h.01" />
                    </svg>
                    <h3>兼容信息</h3>
                  </div>
                </div>
                <div className="plugin-compatibility-card">
                  <ul className="plugin-compatibility-list">
                    {issues.map((issue) => (
                      <li key={issue}>{compatibilityIssue(issue)}</li>
                    ))}
                  </ul>
                </div>
              </section>
            )}
          </div>
        </div>
      </div>
      <SkillDetailDialog
        skill={selectedSkill}
        pendingAction={pendingAction}
        storageReady={storageReady}
        onToggleSkill={onToggleSkill}
        onShowToast={(message, type = "info") => onShowToast(message, type)}
        onClose={() => setSkillId(undefined)}
        getFallbackFocus={() => closeRef.current}
      />
      <McpDetailDialog
        server={selectedServer}
        icon={
          selectedServer?.serverId === "github" ||
          selectedServer?.pluginId === "eidos-github" ||
          selectedServer?.remote?.authType === "github-device" ? (
            <img className="plugin-server-icon-img" src={githubIcon} alt="GitHub" />
          ) : undefined
        }
        onClose={() => setServerId(undefined)}
      />
      <HookHistoryDialog open={hookHistory} sourceId={plugin.id} onClose={() => setHookHistory(false)} />
      <ConfirmDialog
        open={Boolean(reviewing)}
        title="启用 MCP 服务"
        isDestructive
        busy={busy}
        error={error}
        confirmLabel="授权并启用"
        description={
          reviewing && (
            <>
              <p>请检查 {reviewing.serverId} 的启动命令和权限。</p>
              <pre className="plugin-hook-command">{[reviewing.executable, ...reviewing.argv].join(" ")}</pre>
              <p>权限：{reviewing.permissionProfile}</p>
              <p>环境变量：{reviewing.envNames.join(", ") || "无"}</p>
              {reviewing.cwd && <p>工作目录：{reviewing.cwd}</p>}
              <p>
                启动超时：{reviewing.startupTimeoutSeconds} 秒；工具超时：{reviewing.toolTimeoutSeconds} 秒。
              </p>
            </>
          )
        }
        onConfirm={() => {
          if (reviewing) void changeMcp(reviewing, true);
        }}
        onCancel={() => {
          if (!busy) setReviewId(undefined);
        }}
      />
    </>
  );
}
