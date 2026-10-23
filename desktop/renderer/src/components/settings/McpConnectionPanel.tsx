import { useEffect, useRef, useState } from "react";
import type { McpConnectionRequest, McpConnectionState } from "../../../../shared/mcp-connections.js";
import type { McpServerRecord } from "../../contracts.js";
import { Button } from "../Button.js";
import { FieldError } from "../FieldError.js";
import "./McpConnectionPanel.css";

const states = { disconnected: "尚未连接账号", connecting: "等待授权", connected: "账号已连接", expired: "授权已失效", error: "连接失败" };
const errors: Record<string, string> = {
  mcp_login_denied: "你取消了服务端授权，可以重新连接。",
  mcp_login_timeout: "登录已超时，请重新连接。",
  mcp_login_canceled: "登录已取消。",
  mcp_login_interrupted: "上次登录已中断，请重新连接。",
  mcp_login_required: "凭据已失效，请重新连接账号。",
  mcp_access_denied: "当前账号没有访问权限，请检查仓库和组织授权。",
  mcp_connection_failed: "Eidos 无法连接服务，请检查网络、令牌和账号权限。",
};

export function McpConnectionPanel({ server, enabled, onChanged }: {
  server: McpServerRecord; enabled: boolean; onChanged?: (() => Promise<void>) | undefined;
}) {
  const [state, setState] = useState<McpConnectionState>();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const autoOpen = useRef(false);
  const openedUrl = useRef<string | undefined>(undefined);
  const mounted = useRef(true);
  const resourceKey = `${server.pluginId}:${server.serverId}:${server.pluginHash}`;
  const currentResource = useRef(resourceKey);
  currentResource.current = resourceKey;
  const readRequest: McpConnectionRequest = { pluginId: server.pluginId, serverId: server.serverId, action: "read" };
  useEffect(() => {
    mounted.current = true;
    let active = true;
    setState(undefined); setToken(""); setError(undefined); setBusy(false);
    autoOpen.current = false; openedUrl.current = undefined;
    void window.eidosRuntime.mcpConnection(readRequest).then(value => { if (active) setState(value); })
      .catch(() => { if (active) setError("账号状态暂时无法加载。"); });
    return () => { active = false; mounted.current = false; };
  }, [resourceKey]);

  useEffect(() => {
    if (state?.status !== "connecting") return;
    let active = true;
    const timer = window.setTimeout(() => {
      void window.eidosRuntime.mcpConnection(readRequest).then(async next => {
        if (!active) return;
        setState(next);
        if (next.status !== "connecting") await onChanged?.();
      }).catch(() => { if (active) setError("Eidos 暂时无法读取登录结果，请重试。"); });
    }, 1000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [state, resourceKey]);

  useEffect(() => {
    const url = state?.authorizationUrl;
    if (!url || !autoOpen.current || openedUrl.current === url) return;
    openedUrl.current = url;
    void window.eidosRuntime.openMcpLogin(readRequest).catch(() => setError("浏览器未能打开，请点击“打开授权页面”。"));
  }, [state?.authorizationUrl]);

  async function act(action: "connect" | "cancel" | "disconnect", method: "browser" | "token" = "browser") {
    const identity = resourceKey;
    setBusy(true); setError(undefined);
    autoOpen.current = action === "connect" && method === "browser";
    openedUrl.current = undefined;
    try {
      const next = await window.eidosRuntime.mcpConnection({ ...readRequest, action, method,
        ...(action === "connect" ? { operationId: crypto.randomUUID() } : {}),
        ...(method === "token" ? { token } : {}),
      });
      if (!mounted.current || currentResource.current !== identity) return;
      setToken(""); setState(next);
      await onChanged?.();
    } catch (cause) {
      if (mounted.current && currentResource.current === identity) setError(cause instanceof Error && cause.message.includes("GITHUB_CLIENT_ID_REQUIRED")
        ? "当前版本尚未配置 GitHub 浏览器登录，请使用个人访问令牌连接。" : "连接操作失败，请稍后重试。");
    } finally { if (mounted.current && currentResource.current === identity) setBusy(false); }
  }

  const connecting = state?.status === "connecting";
  const connected = state?.status === "connected";
  const tokenForm = <form onSubmit={event => { event.preventDefault(); void act("connect", "token"); }}>
    <label htmlFor={`mcp-token-${server.serverId}`}>个人访问令牌</label>
    <input id={`mcp-token-${server.serverId}`} type="password" value={token} autoComplete="off" spellCheck={false}
      disabled={busy || !enabled || connecting} maxLength={8192} onChange={event => setToken(event.target.value)} />
    <Button type="submit" variant="secondary" size="small" disabled={busy || !enabled || connecting || !token.trim()}>使用令牌连接</Button>
    {server.remote?.authType === "github-device" && <Button type="button" variant="secondary" size="small"
      onClick={() => void window.eidosRuntime.openMcpLogin({ ...readRequest, method: "token" }).catch(() => setError("GitHub 令牌设置未能打开。"))}>创建 GitHub 令牌</Button>}
  </form>;

  return <section className="mcp-connection-panel" aria-label={`${server.serverId} 账号连接`}>
    <p>{server.remote?.url}</p>
    <div className="mcp-connection-heading"><strong>账号连接</strong><span role="status">{state ? states[state.status ?? "disconnected"] : "正在读取账号状态…"}{state?.accountLabel ? ` · ${state.accountLabel}` : ""}</span></div>
    {error && <FieldError error={error} />}
    {state?.errorCode && <FieldError error={errors[state.errorCode] || "连接未完成，请重新连接。"} />}
    {!enabled && <p>请先启用插件，再连接账号。</p>}
    {connecting ? <>
      <p>{state.userCode ? "请在 GitHub 授权页面输入以下代码。" : state.authorizationUrl ? "请在浏览器中完成授权。" : "Eidos 正在准备连接…"}</p>
      {state.userCode && <code className="mcp-device-code" aria-label="GitHub 设备授权码">{state.userCode}</code>}
      <div className="mcp-connection-actions">
        {state.authorizationUrl && <Button variant="secondary" size="small" onClick={() => void window.eidosRuntime.openMcpLogin(readRequest).catch(() => setError("授权页面未能打开，请重试。"))}>打开授权页面</Button>}
        <Button variant="secondary" size="small" disabled={busy} onClick={() => void act("cancel")}>取消登录</Button>
      </div>
    </> : connected ? <Button variant="secondary" size="small" disabled={busy} onClick={() => void act("disconnect")}>断开账号连接</Button> : state ? <>
      {state.browserAvailable && <Button variant="primary" size="small" disabled={busy || !enabled} onClick={() => void act("connect")}>在浏览器中登录</Button>}
      {server.remote?.authType !== "oauth" && server.remote?.authType !== "none" && (state.browserAvailable
        ? <details><summary>使用个人访问令牌</summary>{tokenForm}</details> : <>
          {server.remote?.authType === "github-device" && <p>当前版本尚未配置 GitHub 浏览器登录。你可以使用个人访问令牌连接。</p>}{tokenForm}</>)}
      {server.remote?.authType === "none" && <Button variant="secondary" size="small" disabled={busy || !enabled} onClick={() => void act("connect")}>连接服务</Button>}
      <p>账号连接后，任务可以使用此服务。写入操作遵循当前的审批模式。</p>
    </> : null}
  </section>;
}
