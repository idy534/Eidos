import { useEffect, useRef, useState } from "react";
import type { BuiltinPlugin } from "../../../../shared/mcp-connections.js";
import { Button } from "../Button.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { githubIcon } from "./SettingsIcons.js";
import type { BrowserStatus } from "../../../../shared/browser.js";
import { GlobeIcon } from "../MenuIcons.js";

const BROWSER_PLUGIN_ID = "eidos-browser";

function BrowserStatusLine({ status }: { status: BrowserStatus | undefined }) {
  if (!status) return null;
  const parts = [
    status.chromeInstalled ? `Google Chrome ${status.chromeVersion ?? ""}`.trim() : "未找到 Google Chrome，请先安装",
    { ready: "资源已就绪", preparing: "正在准备资源", missing: "资源尚未下载", failed: "资源准备失败，可在资源设置中重试", unavailable: "资源不可用" }[status.resource],
    status.running ? "浏览器运行中" : undefined,
  ].filter(Boolean);
  return <span className="builtin-plugin-status" role="status">{parts.join(" · ")}</span>;
}

export function BuiltinPlugins({ onInstall, onShowToast, catalogRevision }: {
  onInstall: (pluginId: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
  catalogRevision?: string | undefined;
}) {
  const [plugins, setPlugins] = useState<BuiltinPlugin[]>([]);
  const [busy, setBusy] = useState(false);
  const [browser, setBrowser] = useState<BrowserStatus>();
  const [closing, setClosing] = useState(false);
  const onShowToastRef = useRef(onShowToast);
  onShowToastRef.current = onShowToast;

  useEffect(() => {
    let active = true;
    window.eidosRuntime.builtinPlugins({ action: "list" }).then(result => {
      if (active) setPlugins(result.plugins);
    }).catch(() => {
      if (active) onShowToastRef.current("内置插件暂时无法加载，请稍后重试。", "error");
    });
    return () => { active = false; };
  }, [catalogRevision]);

  useEffect(() => {
    let active = true;
    window.eidosRuntime.readBrowserAgentStatus({}).then((status) => {
      if (active) setBrowser(status);
    }).catch(() => {
      if (active) setBrowser(undefined);
    });
    return () => { active = false; };
  }, [catalogRevision, plugins]);

  async function closeBrowser() {
    setClosing(true);
    try {
      await window.eidosRuntime.closeBrowserAgent();
      setBrowser((current) => current && { ...current, running: false });
      onShowToast("已关闭 Eidos 浏览器。所有会话的网页都已关闭，登录状态保留。", "success");
    } catch {
      onShowToast("关闭浏览器失败，请稍后重试。", "error");
    } finally {
      setClosing(false);
    }
  }

  async function install(plugin: BuiltinPlugin) {
    setBusy(true);
    try {
      await onInstall(plugin.id);
      setPlugins((await window.eidosRuntime.builtinPlugins({ action: "list" })).plugins);
      onShowToast(plugin.id === BROWSER_PLUGIN_ID
        ? `${plugin.name} 已${plugin.updateAvailable ? "更新" : "安装"}，请在插件资源中启用浏览器服务。`
        : `${plugin.name} 已${plugin.updateAvailable ? "更新" : "安装"}，请打开插件资源连接账号。`, "success");
    } catch {
      onShowToast(`安装 ${plugin.name} 插件失败，请稍后重试。`, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingSection title="内置插件">
      {plugins.map((plugin) => {
        const isGitHub = plugin.id === "eidos-github" || plugin.name.toLowerCase().includes("github");
        return (
          <SettingRow
            key={plugin.id}
            title={
              <div className="plugin-row-header">
                {isGitHub && <img className="plugin-icon" src={githubIcon} alt="GitHub" />}
                {plugin.id === BROWSER_PLUGIN_ID && <GlobeIcon className="plugin-icon" />}
                <span className="plugin-name">{plugin.name}</span>
              </div>
            }
            description={plugin.description}
            meta={plugin.id === BROWSER_PLUGIN_ID ? <BrowserStatusLine status={browser} /> : undefined}
            action={<>
              <Button
                variant="secondary"
                size="small"
                disabled={busy || (plugin.installed && !plugin.updateAvailable)}
                loading={busy}
                onClick={() => void install(plugin)}
              >
                {plugin.updateAvailable ? "更新" : plugin.installed ? "已安装" : "安装"}
              </Button>
              {plugin.id === BROWSER_PLUGIN_ID && browser?.running && (
                <Button variant="ghost" size="small" loading={closing} disabled={closing} onClick={() => void closeBrowser()}>
                  关闭浏览器
                </Button>
              )}
            </>}
          />
        );
      })}
    </SettingSection>
  );
}
