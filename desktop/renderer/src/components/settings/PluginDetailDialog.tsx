import { useEffect, useRef, useState } from "react";
import type { McpServerRecord, PluginRecord, SkillMetadata } from "../../contracts.js";
import { userFacingError } from "../../session-state.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { McpDetailDialog } from "./McpDetailDialog.js";
import { SkillDetailDialog } from "./SkillDetailDialog.js";
import { compatibilityIssue } from "./plugin-compatibility.js";
import type { SettingsPendingAction } from "./settings-types.js";
import "./PluginDetailDialog.css";

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
  onReviewHooks: () => void;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
  onClose: () => void;
  getFallbackFocus: () => HTMLElement | null;
}

export function PluginDetailDialog({ plugin, skills, servers, pendingAction, storageReady,
  resourceError, obscured, onToggleSkill, onToggleMcp, onReviewHooks, onShowToast, onClose, getFallbackFocus,
}: PluginDetailDialogProps) {
  const [skillId, setSkillId] = useState<string>();
  const [serverId, setServerId] = useState<string>();
  const [reviewId, setReviewId] = useState<string>();
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const pluginSkills = skills.filter(skill => skill.sourceKind === "plugin" && skill.pluginId === plugin?.id);
  const pluginServers = servers.filter(server => server.pluginId === plugin?.id);
  const selectedSkill = pluginSkills.find(skill => skill.qualifiedId === skillId);
  const selectedServer = pluginServers.find(server => server.serverId === serverId) ?? null;
  const reviewing = pluginServers.find(server => server.serverId === reviewId);
  const nested = Boolean(selectedSkill || selectedServer || reviewing || obscured);
  const busy = saving || Boolean(pendingAction);
  useDialogFocusLifecycle({ open: Boolean(plugin), initialFocusRef: closeRef, getFallbackFocus });

  useEffect(() => {
    setSkillId(undefined); setServerId(undefined); setReviewId(undefined); setError(undefined);
  }, [plugin?.id]);

  useEffect(() => {
    if (!plugin || nested) return;
    function keydown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        if (!busy) onClose();
      }
      if (event.key !== "Tab") return;
      const targets = dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), summary, [tabindex='0']");
      const first = targets?.[0];
      const last = targets?.[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [plugin, nested, busy, onClose]);

  async function changeMcp(server: McpServerRecord, enabled: boolean) {
    if (!onToggleMcp || busy) return;
    setSaving(true); setError(undefined);
    try {
      await onToggleMcp(server.pluginId, server.serverId, enabled);
      setReviewId(undefined);
      onShowToast(enabled ? "MCP 服务已授权并启用" : "MCP 服务已停用", "info");
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setSaving(false); }
  }

  if (!plugin) return null;
  const hooks = plugin.hookReview?.definitions ?? [];
  const issues = plugin.compatibility?.issues ?? [];
  return <>
    <div className="modal-backdrop" inert={nested} aria-hidden={nested || undefined} onClick={() => { if (!busy) onClose(); }}>
      <div className="modal-dialog plugin-resource-dialog" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="plugin-resource-title" onClick={event => event.stopPropagation()}>
        <header className="plugin-resource-header">
          <div><h2 id="plugin-resource-title">{plugin.name}</h2><p>{plugin.enabled ? "已启用" : "已停用"} · {plugin.compatibility && !plugin.compatibility.declaredVersion ? "未声明版本" : `v${plugin.version}`}</p></div>
          <button ref={closeRef} type="button" className="skill-detail-close-btn" aria-label="关闭插件详情" disabled={busy} onClick={onClose}>×</button>
        </header>
        <div className="plugin-resource-body" tabIndex={0} aria-label="插件资源">
          <p>{plugin.description || "插件未提供说明。"}</p>
          {!plugin.enabled && <p role="status">插件已停用。启用插件后，已开启并获得授权的资源才会生效。</p>}
          {resourceError && <AlertBanner tone="error">{resourceError}</AlertBanner>}
          {error && !reviewing && <AlertBanner tone="error">{error}</AlertBanner>}
          <section className="plugin-resource-section" aria-label="插件技能">
            <h3>Skills <span>{pluginSkills.length}</span></h3>
            {pluginSkills.map(skill => <button key={skill.qualifiedId} type="button" className="plugin-resource-item plugin-resource-link" onClick={() => setSkillId(skill.qualifiedId)}>
              <span><strong>{skill.name}</strong><span className="plugin-resource-description">{skill.description || "未提供技能说明"}</span></span>
              <span className="plugin-resource-state">{skill.enabled ? "已开启" : "已关闭"}</span>
            </button>)}
            {!pluginSkills.length && !resourceError && <p className="plugin-resource-empty">插件未提供 Skill。</p>}
          </section>
          <section className="plugin-resource-section" aria-label="插件 MCP 服务">
            <h3>MCP Servers <span>{pluginServers.length}</span></h3>
            {pluginServers.map(server => <div key={server.serverId} className="plugin-resource-item">
              <button type="button" className="plugin-resource-link plugin-resource-server" aria-label={`查看 ${server.serverId} 的工具`} onClick={() => setServerId(server.serverId)}>
                <strong>{server.serverId}</strong><span className="plugin-resource-description">{server.errorCode || (!server.declaredEnabled ? "插件未开放此服务" : server.available ? "可用" : server.consented ? "已授权，暂不可用" : "等待授权")}</span>
              </button>
              <Button variant="secondary" size="small" disabled={busy || !storageReady || !onToggleMcp || (!server.consented && (!plugin.enabled || !server.declaredEnabled))}
                onClick={() => { setError(undefined); if (server.consented) void changeMcp(server, false); else setReviewId(server.serverId); }}>{server.consented ? "停用" : "启用"}</Button>
            </div>)}
            {!pluginServers.length && !resourceError && <p className="plugin-resource-empty">插件没有已接入的 MCP 服务。</p>}
          </section>
          <section className="plugin-resource-section" aria-label="插件 Hook">
            <div className="plugin-resource-section-heading"><h3>Hooks <span>{hooks.length}</span></h3>{hooks.length > 0 && <Button variant="secondary" size="small" disabled={busy} onClick={onReviewHooks}>审阅 Hook</Button>}</div>
            {hooks.length > 0 && <p>{plugin.hookReview?.trusted ? "此版本的 Hook 已信任。" : "Hook 需要用户审阅并信任后才会执行。"}</p>}
            {hooks.map(hook => <details className="plugin-resource-hook" key={hook.key}><summary>{hook.event}{hook.matcher ? ` · ${hook.matcher}` : ""} · {hook.handlerType}{hook.background ? " · 异步" : ""}</summary><pre className="plugin-hook-command">{hook.command || `处理器：${hook.handlerType}`}</pre></details>)}
            {!hooks.length && !resourceError && <p className="plugin-resource-empty">插件未提供 Hook。</p>}
          </section>
          {issues.length > 0 && <section className="plugin-resource-section" aria-label="插件兼容信息"><h3>兼容信息</h3><ul>{issues.map(issue => <li key={issue}>{compatibilityIssue(issue)}</li>)}</ul></section>}
        </div>
      </div>
    </div>
    <SkillDetailDialog skill={selectedSkill} pendingAction={pendingAction} storageReady={storageReady} onToggleSkill={onToggleSkill} onShowToast={(message, type = "info") => onShowToast(message, type)} onClose={() => setSkillId(undefined)} getFallbackFocus={() => closeRef.current} />
    <McpDetailDialog server={selectedServer} onClose={() => setServerId(undefined)} />
    <ConfirmDialog open={Boolean(reviewing)} title="启用 MCP 服务" isDestructive busy={busy} error={error} confirmLabel="授权并启用"
      description={reviewing && <><p>请检查 {reviewing.serverId} 的启动命令和权限。</p><pre className="plugin-hook-command">{[reviewing.executable, ...reviewing.argv].join(" ")}</pre><p>权限：{reviewing.permissionProfile}</p><p>环境变量：{reviewing.envNames.join(", ") || "无"}</p>{reviewing.cwd && <p>工作目录：{reviewing.cwd}</p>}<p>启动超时：{reviewing.startupTimeoutSeconds} 秒；工具超时：{reviewing.toolTimeoutSeconds} 秒。</p></>}
      onConfirm={() => { if (reviewing) void changeMcp(reviewing, true); }} onCancel={() => { if (!busy) setReviewId(undefined); }} />
  </>;
}
