import { useId, useState } from "react";
import type { BrowserStatus, BrowserTargetRequest } from "../../../../shared/browser.js";
import { Button } from "../Button.js";
import { EyeIcon, EyeOffIcon } from "./ChannelIcons.js";
import { StatusBadge } from "./StatusBadge.js";

type BrowserTarget = BrowserTargetRequest["target"];

const OPTIONS: Array<{ target: BrowserTarget; title: string; description: string; recommended?: boolean }> = [
  {
    target: "chrome_extension",
    title: "我的 Chrome（标签组）",
    description: "通过 Playwright Extension 使用你日常的 Chrome 和登录状态。Agent 只操作自己的标签组；设置完成前使用 Eidos 浏览器。",
    recommended: true,
  },
  {
    target: "eidos",
    title: "Eidos 浏览器",
    description: "独立的 Chrome 窗口和配置，只包含你在其中登录的网站。",
  },
  {
    target: "user_chrome",
    title: "我的 Chrome（远程调试）",
    description: "需要在 Chrome 中开启远程调试，每次连接都要在 Chrome 中确认；Agent 能看到全部标签页。",
  },
];

const USER_CHROME_RISK = "Agent 将操作你日常使用的 Google Chrome，并能使用其中所有已登录的账号，包括邮箱、支付和工作账号。你需要先在 Chrome 中打开 chrome://inspect/#remote-debugging 开启远程调试；Chrome 每次连接时也会请你确认。每个会话第一次操作网页前，Eidos 会以高危操作请你确认。";
const EXTENSION_RISK = "保存后，Agent 可以通过 Playwright Extension 操作你日常使用的 Chrome。标签组只限定 Agent 操作哪些标签页，不隔离账号：Agent 可以打开你已登录的网站，包括邮箱、支付和工作账号，也能读取这些网站的 Cookie，下载也不会被拦截。每个会话第一次操作网页前，Eidos 仍会以高危操作请你确认。";

const PRESENCE_TEXT: Record<NonNullable<BrowserStatus["extensionPresence"]>, string> = {
  installed: "已在 Chrome 中检测到扩展",
  missing: "未在 Chrome 中检测到扩展",
  unknown: "无法读取 Chrome 配置，请确认已安装扩展",
};

/** Which Chrome the Agent drives, and the Playwright Extension setup for the user's own Chrome. */
export function BrowserTargetPanel({ status, onStatusChange, onShowToast }: {
  status: BrowserStatus;
  onStatusChange: () => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}) {
  const name = useId();
  const target = status.target ?? "chrome_extension";
  const configured = status.extensionConfigured === true;
  const presence = status.extensionPresence ?? "unknown";
  const [pending, setPending] = useState<"target" | "token" | "clear" | null>(null);
  const [confirm, setConfirm] = useState<"user_chrome" | "token" | null>(null);
  const [token, setToken] = useState("");
  const [visible, setVisible] = useState(false);
  const [editing, setEditing] = useState(false);
  const [tokenError, setTokenError] = useState("");

  function selectTarget(next: BrowserTarget) {
    if (next === target || pending) return;
    // Remote debugging hands over every tab; only the danger button applies it.
    if (next === "user_chrome") {
      setConfirm("user_chrome");
      return;
    }
    void applyTarget(next);
  }

  async function applyTarget(next: BrowserTarget) {
    setConfirm(null);
    setPending("target");
    try {
      const response = await window.eidosRuntime.setBrowserAgentTarget({ target: next });
      await onStatusChange();
      const revoked = response.revokedGrants > 0 ? "所有会话对你日常 Chrome 的授权已撤销。" : "";
      onShowToast({
        chrome_extension: `之后的任务优先使用你日常的 Chrome 和标签组。${revoked}`,
        eidos: `之后的任务使用 Eidos 浏览器。${revoked}`,
        user_chrome: "之后的任务会通过远程调试使用你日常的 Chrome。每个会话第一次操作网页前都需要你确认。",
      }[next], "success");
    } catch {
      onShowToast("切换浏览器失败，请稍后重试。", "error");
    } finally {
      setPending(null);
    }
  }

  async function saveToken() {
    setConfirm(null);
    setPending("token");
    try {
      await window.eidosRuntime.setBrowserAgentExtensionToken({ token: token.trim() });
      setToken("");
      setVisible(false);
      setEditing(false);
      await onStatusChange();
      onShowToast("令牌已保存。新任务会在你的 Chrome 中使用 Agent 自己的标签组。", "success");
    } catch (cause) {
      const invalid = cause instanceof Error && cause.message.includes("BROWSER_EXTENSION_TOKEN_INVALID");
      setTokenError(invalid ? "令牌格式不正确，请粘贴扩展页面中复制的内容。" : "令牌保存失败，请稍后重试。");
    } finally {
      setPending(null);
    }
  }

  async function clearToken() {
    setPending("clear");
    try {
      await window.eidosRuntime.clearBrowserAgentExtensionToken();
      setEditing(false);
      await onStatusChange();
      onShowToast("已移除令牌，新任务改用 Eidos 浏览器，所有会话对你日常 Chrome 的授权已撤销。", "success");
    } catch {
      onShowToast("移除令牌失败，请稍后重试。", "error");
    } finally {
      setPending(null);
    }
  }

  async function openStore() {
    try {
      await window.eidosRuntime.openBrowserExtensionStore();
    } catch {
      onShowToast("无法打开 Chrome 应用商店，请稍后重试。", "error");
    }
  }

  function requestSave() {
    if (!token.trim()) {
      setTokenError("请先粘贴令牌。");
      return;
    }
    setTokenError("");
    setConfirm("token");
  }

  const active = status.effectiveTarget === "chrome_extension";
  return (
    <div className="browser-target">
      <fieldset className="browser-target-options" disabled={pending !== null}>
        <legend>Agent 使用的浏览器</legend>
        {OPTIONS.map((option) => (
          <label
            key={option.target}
            className={`browser-target-option${target === option.target ? " browser-target-option--selected" : ""}`}
          >
            <input
              type="radio"
              name={name}
              value={option.target}
              checked={target === option.target}
              aria-labelledby={`${name}-${option.target}-title`}
              aria-describedby={`${name}-${option.target}-desc`}
              onChange={() => selectTarget(option.target)}
            />
            <span className="browser-target-option-text">
              <span className="browser-target-option-title">
                <span id={`${name}-${option.target}-title`}>{option.title}</span>
                {option.recommended && <StatusBadge tone="info" dot={false}>推荐</StatusBadge>}
              </span>
              <span id={`${name}-${option.target}-desc`} className="browser-target-option-desc">{option.description}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {confirm === "user_chrome" && (
        <div className="builtin-plugin-confirm" role="alertdialog" aria-label="使用我的 Chrome（远程调试）">
          <p>{USER_CHROME_RISK}</p>
          <div className="builtin-plugin-confirm-actions">
            <Button variant="ghost" size="small" onClick={() => setConfirm(null)}>取消</Button>
            <Button variant="danger" size="small" onClick={() => void applyTarget("user_chrome")}>使用我的 Chrome</Button>
          </div>
        </div>
      )}

      {target === "chrome_extension" && (
        <section className="browser-extension-setup" aria-label="设置 Playwright Extension">
          <ol className="browser-extension-steps">
            <li>
              <span className="browser-extension-step-title">安装 Playwright Extension</span>
              <span className={`browser-extension-step-state${presence === "installed" ? " browser-extension-step-state--done" : ""}`}>
                {PRESENCE_TEXT[presence]}
              </span>
              {presence !== "installed" && (
                <Button variant="ghost" size="small" onClick={() => void openStore()}>打开 Chrome 应用商店 ↗</Button>
              )}
            </li>
            <li>
              <span className="browser-extension-step-title">保存连接令牌</span>
              {configured && !editing ? (
                <>
                  <span className="browser-extension-step-state browser-extension-step-state--done">令牌已保存</span>
                  <span className="browser-extension-step-actions">
                    <Button variant="ghost" size="small" disabled={pending !== null} onClick={() => setEditing(true)}>更换</Button>
                    <Button variant="ghost" size="small" className="browser-extension-remove" disabled={pending !== null}
                      loading={pending === "clear"} onClick={() => void clearToken()}>移除</Button>
                  </span>
                </>
              ) : (
                <>
                  <span className="browser-extension-step-hint">
                    点击 Chrome 工具栏中的 Playwright Extension 图标，在打开的页面复制令牌，整行粘贴即可。
                  </span>
                  <span className="browser-extension-token-row">
                    <span className="web-search-input-field">
                      <input
                        aria-label="Playwright Extension 令牌"
                        autoComplete="new-password"
                        spellCheck={false}
                        type={visible ? "text" : "password"}
                        maxLength={512}
                        value={token}
                        placeholder="粘贴令牌"
                        aria-invalid={tokenError ? true : undefined}
                        onChange={(event) => { setToken(event.target.value); setTokenError(""); }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") { event.preventDefault(); requestSave(); }
                        }}
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
                    <Button variant="secondary" size="small" loading={pending === "token"} disabled={pending !== null}
                      onClick={requestSave}>保存</Button>
                    {editing && (
                      <Button variant="ghost" size="small" disabled={pending !== null}
                        onClick={() => { setEditing(false); setToken(""); setTokenError(""); }}>取消</Button>
                    )}
                  </span>
                  {tokenError && <span className="browser-extension-error" role="alert">{tokenError}</span>}
                </>
              )}
            </li>
          </ol>
          {confirm === "token" && (
            <div className="builtin-plugin-confirm" role="alertdialog" aria-label="启用我的 Chrome">
              <p>{EXTENSION_RISK}</p>
              <div className="builtin-plugin-confirm-actions">
                <Button variant="ghost" size="small" onClick={() => setConfirm(null)}>取消</Button>
                <Button variant="danger" size="small" onClick={() => void saveToken()}>保存并启用</Button>
              </div>
            </div>
          )}
          <p className={`browser-extension-summary${active ? " browser-extension-summary--active" : ""}`} role="status">
            {active
              ? "已启用。新任务会在你的 Chrome 中创建「Playwright · Eidos · 会话名」标签组，每个会话第一次操作网页前需要你确认。"
              : "设置完成前，新任务使用 Eidos 浏览器。"}
          </p>
        </section>
      )}
    </div>
  );
}
