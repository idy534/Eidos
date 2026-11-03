import { useEffect, useRef, useState } from "react";
import type { BuiltinPlugin } from "../../../../shared/mcp-connections.js";
import { Button } from "../Button.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { githubIcon } from "./SettingsIcons.js";
import type { BrowserStatus } from "../../../../shared/browser.js";
import { GlobeIcon } from "../MenuIcons.js";

const BROWSER_PLUGIN_ID = "eidos-browser";
const BROWSER_ERRORS: Record<string, string> = {
  browser_unavailable: "未找到 Google Chrome，请先安装 Google Chrome。",
  browser_profile_in_use: "Eidos 浏览器的数据正被另一个 Chrome 窗口使用，请先关闭该窗口。",
  browser_start_failed: "浏览器未能启动，请稍后重试。",
};

function BrowserStatusLine({ status }: { status: BrowserStatus | undefined }) {
  if (!status) return null;
  if (status.target === "user_chrome") {
    return <span className="builtin-plugin-status" role="status">{status.attachAvailable
      ? "Agent 使用你日常的 Chrome · 远程调试已开启"
      : "Agent 使用你日常的 Chrome · 请在 Chrome 打开 chrome://inspect/#remote-debugging 开启远程调试"}</span>;
  }
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
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState(false);
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

  async function openBrowser() {
    setClosing(true);
    try {
      const result = await window.eidosRuntime.openBrowserAgent();
      if (result.opened) {
        setBrowser((current) => current && { ...current, running: true });
        onShowToast("已打开 Eidos 浏览器。登录需要的网站后，Agent 可以在授权后使用这些账号。", "success");
      } else {
        onShowToast(BROWSER_ERRORS[result.errorCode ?? ""] ?? "浏览器未能打开，请稍后重试。", "error");
      }
    } catch {
      onShowToast("浏览器未能打开，请稍后重试。", "error");
    } finally {
      setClosing(false);
    }
  }

  async function setTarget(target: "eidos" | "user_chrome") {
    setConfirmTarget(false);
    setClosing(true);
    try {
      await window.eidosRuntime.setBrowserAgentTarget({ target });
      setBrowser(await window.eidosRuntime.readBrowserAgentStatus({}));
      onShowToast(target === "user_chrome"
        ? "之后的任务会使用你日常的 Chrome。每个会话第一次操作网页前都需要你确认。"
        : "已改回 Eidos 浏览器，所有会话对你日常 Chrome 的授权已撤销。", "success");
    } catch {
      onShowToast("切换浏览器失败，请稍后重试。", "error");
    } finally {
      setClosing(false);
    }
  }

  async function clearProfile() {
    setConfirmClear(false);
    setClosing(true);
    try {
      const result = await window.eidosRuntime.clearBrowserAgentProfile();
      if (result.errorCode) {
        onShowToast(BROWSER_ERRORS[result.errorCode] ?? "清除浏览器数据失败，请稍后重试。", "error");
      } else {
        setBrowser((current) => current && { ...current, running: false });
        onShowToast("已清除 Eidos 浏览器的登录状态和网站数据，所有会话的浏览器授权已撤销。", "success");
      }
    } catch {
      onShowToast("清除浏览器数据失败，请稍后重试。", "error");
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
              {plugin.id === BROWSER_PLUGIN_ID && plugin.installed && browser && (
                <Button variant={browser.target === "user_chrome" ? "secondary" : "ghost"} size="small" disabled={closing}
                  onClick={() => browser.target === "user_chrome" ? void setTarget("eidos") : setConfirmTarget(true)}>
                  {browser.target === "user_chrome" ? "改回 Eidos 浏览器" : "使用我的 Chrome"}
                </Button>
              )}
              {plugin.id === BROWSER_PLUGIN_ID && plugin.installed && browser?.chromeInstalled && browser.target !== "user_chrome" && (
                browser.running
                  ? <Button variant="ghost" size="small" loading={closing} disabled={closing} onClick={() => void closeBrowser()}>
                    关闭浏览器
                  </Button>
                  : <Button variant="ghost" size="small" loading={closing} disabled={closing} onClick={() => void openBrowser()}>
                    打开浏览器登录
                  </Button>
              )}
              {plugin.id === BROWSER_PLUGIN_ID && plugin.installed && (
                <Button variant="ghost" size="small" disabled={closing} onClick={() => setConfirmClear(true)}>
                  清除浏览器数据
                </Button>
              )}
            </>}
          />
        );
      })}
      {confirmTarget && (
        <div className="builtin-plugin-confirm" role="alertdialog" aria-label="使用我的 Chrome">
          <p>Agent 将操作你日常使用的 Google Chrome，并能使用其中所有已登录的账号，包括邮箱、支付和工作账号。你需要先在 Chrome 中打开 chrome://inspect/#remote-debugging 开启远程调试；Chrome 每次连接时也会请你确认。每个会话第一次操作网页前，Eidos 会以高危操作请你确认。</p>
          <div className="builtin-plugin-confirm-actions">
            <Button variant="ghost" size="small" onClick={() => setConfirmTarget(false)}>取消</Button>
            <Button variant="danger" size="small" onClick={() => void setTarget("user_chrome")}>使用我的 Chrome</Button>
          </div>
        </div>
      )}
      {confirmClear && (
        <div className="builtin-plugin-confirm" role="alertdialog" aria-label="清除浏览器数据">
          <p>将关闭 Eidos 浏览器并删除其中的登录状态、Cookie 和网站数据，所有会话的浏览器授权也会撤销。此操作无法撤回。</p>
          <div className="builtin-plugin-confirm-actions">
            <Button variant="ghost" size="small" onClick={() => setConfirmClear(false)}>取消</Button>
            <Button variant="danger" size="small" onClick={() => void clearProfile()}>清除</Button>
          </div>
        </div>
      )}
    </SettingSection>
  );
}
