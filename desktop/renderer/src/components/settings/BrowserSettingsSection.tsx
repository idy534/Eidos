import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { BrowserStatus, BrowserTargetRequest } from "../../../../shared/browser.js";
import { Button } from "../Button.js";
import { GlobeIcon } from "../MenuIcons.js";
import { EyeIcon, EyeOffIcon } from "./ChannelIcons.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { SettingRow } from "./SettingRow.js";
import "./BrowserSettings.css";

export const BROWSER_PLUGIN_ID = "eidos-browser";

type BrowserTarget = BrowserTargetRequest["target"];
type Toast = (message: string, type: "success" | "info" | "error") => void;

const TARGETS: Array<{ value: BrowserTarget; label: string }> = [
  { value: "chrome_extension", label: "Chrome（标签组）" },
  { value: "eidos", label: "Eidos 浏览器" },
  { value: "user_chrome", label: "Chrome（远程调试）" },
];
const TARGET_TOASTS: Record<BrowserTarget, string> = {
  chrome_extension: "已切换到你的 Chrome（标签组）",
  eidos: "已切换到 Eidos 浏览器",
  user_chrome: "已切换到你的 Chrome（远程调试）",
};
const RESOURCE_TEXT: Partial<Record<BrowserStatus["resource"], string>> = {
  preparing: "正在准备",
  missing: "尚未下载",
  failed: "下载失败",
  unavailable: "不可用",
};

function targetDescription(status: BrowserStatus): string {
  const target = status.target ?? "chrome_extension";
  if (target === "chrome_extension") {
    return status.effectiveTarget === "chrome_extension"
      ? "Agent 在你的 Chrome 中使用自己的标签组"
      : "完成下方设置前，使用 Eidos 浏览器";
  }
  if (target === "user_chrome") {
    return status.attachAvailable ? "Agent 可以操作你 Chrome 中的全部标签页" : "请先在 chrome://inspect/#remote-debugging 开启远程调试";
  }
  return "独立的 Chrome 配置，不使用你日常的登录状态";
}

/** Browser settings at the top of the browser plugin's detail dialog. */
export function BrowserSettingsSection({ onShowToast, onDialogChange }: {
  onShowToast: Toast;
  /** The confirmations are modal; the parent dialog yields keyboard handling while one is open. */
  onDialogChange?: ((open: boolean) => void) | undefined;
}) {
  const [status, setStatus] = useState<BrowserStatus>();
  const [loadFailed, setLoadFailed] = useState(false);
  const [pending, setPending] = useState(false);
  const [confirm, setConfirm] = useState<"user_chrome" | "token" | null>(null);
  const [token, setToken] = useState("");
  const [visible, setVisible] = useState(false);
  const [editing, setEditing] = useState(false);
  const mounted = useRef(true);
  const tokenRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      onDialogChange?.(false);
    };
  }, []);

  async function load() {
    setLoadFailed(false);
    try {
      const next = await window.eidosRuntime.readBrowserAgentStatus({});
      if (mounted.current) setStatus(next);
    } catch {
      if (mounted.current) setLoadFailed(true);
    }
  }

  async function refresh() {
    try {
      const next = await window.eidosRuntime.readBrowserAgentStatus({});
      if (mounted.current) setStatus(next);
    } catch {
      // The change itself succeeded; keep the last status until the next read.
    }
  }

  function openConfirm(kind: "user_chrome" | "token") {
    setConfirm(kind);
    onDialogChange?.(true);
  }

  function closeConfirm() {
    setConfirm(null);
    onDialogChange?.(false);
  }

  function selectTarget(next: BrowserTarget) {
    if (next === (status?.target ?? "chrome_extension") || pending) return;
    // Remote debugging hands over every tab and account, so it needs its own confirmation.
    if (next === "user_chrome") openConfirm("user_chrome");
    else void applyTarget(next);
  }

  async function applyTarget(next: BrowserTarget) {
    closeConfirm();
    setPending(true);
    try {
      const response = await window.eidosRuntime.setBrowserAgentTarget({ target: next });
      await refresh();
      onShowToast(response.revokedGrants > 0 ? `${TARGET_TOASTS[next]}，各会话的浏览器授权已撤销` : TARGET_TOASTS[next], "success");
    } catch {
      onShowToast("切换浏览器失败，请稍后重试", "error");
    } finally {
      if (mounted.current) setPending(false);
    }
  }

  async function saveToken() {
    closeConfirm();
    setPending(true);
    try {
      await window.eidosRuntime.setBrowserAgentExtensionToken({ token: token.trim() });
      setToken("");
      setVisible(false);
      setEditing(false);
      await refresh();
      onShowToast("令牌已保存，新任务将使用你的 Chrome", "success");
    } catch (cause) {
      const invalid = cause instanceof Error && cause.message.includes("BROWSER_EXTENSION_TOKEN_INVALID");
      onShowToast(invalid ? "令牌格式不正确，请重新复制扩展页面中的令牌" : "令牌保存失败，请稍后重试", "error");
      tokenRef.current?.focus();
    } finally {
      if (mounted.current) setPending(false);
    }
  }

  async function clearToken() {
    setPending(true);
    try {
      await window.eidosRuntime.clearBrowserAgentExtensionToken();
      setEditing(false);
      await refresh();
      onShowToast("令牌已移除，新任务将使用 Eidos 浏览器", "success");
    } catch {
      onShowToast("移除令牌失败，请稍后重试", "error");
    } finally {
      if (mounted.current) setPending(false);
    }
  }

  async function retryResources() {
    try {
      const response = await window.eidosRuntime.retryResource({ packId: "browser" });
      if (!response.accepted) throw new Error("not accepted");
      setStatus((current) => current && { ...current, resource: "preparing" });
      onShowToast("正在下载浏览器资源", "info");
    } catch {
      onShowToast("浏览器资源暂时无法下载，请检查网络后重试", "error");
    }
  }

  async function openStore() {
    try {
      await window.eidosRuntime.openBrowserExtensionStore();
    } catch {
      onShowToast("无法打开 Chrome 应用商店，请稍后重试", "error");
    }
  }

  const target = status?.target ?? "chrome_extension";
  const configured = status?.extensionConfigured === true;
  const resourceText = status ? RESOURCE_TEXT[status.resource] : undefined;

  return (
    <section className="plugin-resource-section" aria-label="浏览器设置">
      <div className="plugin-section-header">
        <div className="plugin-section-title-wrap">
          <GlobeIcon className="plugin-section-icon" />
          <h3>浏览器</h3>
        </div>
      </div>

      {!status ? (
        <div className="browser-settings-card browser-settings-card--message">
          {loadFailed ? (
            <>
              <span>无法读取浏览器设置</span>
              <Button variant="secondary" size="small" onClick={() => void load()}>重试</Button>
            </>
          ) : <span>正在读取…</span>}
        </div>
      ) : (
        <div className="browser-settings-card">
          <SettingRow
            title="Agent 使用的浏览器"
            description={targetDescription(status)}
            action={
              <div className="model-select-control browser-settings-select">
                <select
                  aria-label="Agent 使用的浏览器"
                  value={target}
                  disabled={pending}
                  onChange={(event) => selectTarget(event.target.value as BrowserTarget)}
                >
                  {TARGETS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <span className="model-select-control__chevron" aria-hidden="true">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                </span>
              </div>
            }
          />

          {target === "chrome_extension" && (
            <>
              <SettingRow
                title="Playwright Extension"
                description={status.extensionPresence === "installed" ? "已安装" : "在 Chrome 中安装扩展"}
                action={status.extensionPresence !== "installed" && (
                  <Button variant="secondary" size="small" onClick={() => void openStore()}>Chrome 应用商店 ↗</Button>
                )}
              />
              <SettingRow
                className="browser-settings-token"
                title="连接令牌"
                description={configured && !editing ? "已保存" : "点击扩展图标，复制页面中的令牌"}
                action={configured && !editing ? (
                  <>
                    <Button variant="ghost" size="small" disabled={pending} onClick={() => setEditing(true)}>更换</Button>
                    <Button variant="ghost" size="small" className="browser-settings-danger" disabled={pending}
                      onClick={() => void clearToken()}>移除</Button>
                  </>
                ) : (
                  <form
                    className="browser-settings-token-form"
                    onSubmit={(event) => { event.preventDefault(); if (token.trim()) openConfirm("token"); }}
                  >
                    <span className="web-search-input-field">
                      <input
                        ref={tokenRef}
                        aria-label="Playwright Extension 令牌"
                        autoComplete="new-password"
                        spellCheck={false}
                        type={visible ? "text" : "password"}
                        maxLength={512}
                        value={token}
                        placeholder="粘贴令牌"
                        onChange={(event) => setToken(event.target.value)}
                      />
                      <button
                        type="button"
                        className="web-search-eye-btn"
                        aria-pressed={visible}
                        aria-label={visible ? "隐藏令牌" : "显示令牌"}
                        disabled={!token}
                        onClick={() => setVisible((value) => !value)}
                      >
                        {visible ? <EyeOffIcon /> : <EyeIcon />}
                      </button>
                    </span>
                    <Button type="submit" variant="secondary" size="small" loading={pending} disabled={pending || !token.trim()}>保存</Button>
                    {editing && (
                      <Button variant="ghost" size="small" disabled={pending}
                        onClick={() => { setEditing(false); setToken(""); }}>取消</Button>
                    )}
                  </form>
                )}
              />
            </>
          )}

          {!status.chromeInstalled && (
            <SettingRow title="Google Chrome" description="未找到，请先安装 Google Chrome" />
          )}
          {resourceText && (
            <SettingRow
              title="浏览器资源"
              description={resourceText}
              action={(status.resource === "failed" || status.resource === "missing") && (
                <Button variant="secondary" size="small" onClick={() => void retryResources()}>
                  {status.resource === "failed" ? "重试" : "下载"}
                </Button>
              )}
            />
          )}
        </div>
      )}

      {createPortal(
        <>
          <ConfirmDialog
            open={confirm === "token"}
            title="使用你的 Chrome"
            isDestructive
            confirmLabel="保存"
            description={<p>Agent 将能使用你在 Chrome 中已登录的账号，读取这些网站的 Cookie，下载也不会被拦截。标签组只限定 Agent 操作的标签页，不隔离账号。</p>}
            onConfirm={() => void saveToken()}
            onCancel={closeConfirm}
          />
          <ConfirmDialog
            open={confirm === "user_chrome"}
            title="使用远程调试"
            isDestructive
            confirmLabel="切换"
            description={<p>Agent 将能看到并操作你 Chrome 中的全部标签页和已登录账号。需要先在 chrome://inspect/#remote-debugging 开启远程调试，Chrome 每次连接时也会请你确认。</p>}
            onConfirm={() => void applyTarget("user_chrome")}
            onCancel={closeConfirm}
          />
        </>,
        document.body,
      )}
    </section>
  );
}
