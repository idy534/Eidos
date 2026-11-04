import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserGrantIndicator } from "./BrowserGrantIndicator.js";
import { BuiltinPlugins } from "./settings/BuiltinPlugins.js";
import type { RuntimeNotification } from "../../../shared/domain-contracts.js";

const status = {
  chromeInstalled: true, chromeVersion: "156.0", running: false, pluginInstalled: true, pluginEnabled: true,
  resource: "ready" as const, granted: true,
};
let listeners: ((notification: RuntimeNotification) => void)[] = [];

function api(value: object) {
  Object.defineProperty(window, "eidosRuntime", {
    configurable: true,
    value: {
      onNotification: (callback: (notification: RuntimeNotification) => void) => {
        listeners.push(callback);
        return () => { listeners = listeners.filter((listener) => listener !== callback); };
      },
      ...value,
    },
  });
}

beforeEach(() => { listeners = []; });

describe("Browser grant indicator", () => {
  it("shows the Session grant, follows notifications and revokes it", async () => {
    const revokeBrowserAgentGrant = vi.fn().mockResolvedValue({ revoked: true });
    const onShowToast = vi.fn();
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue(status), revokeBrowserAgentGrant });
    render(<BrowserGrantIndicator sessionId="s1" onShowToast={onShowToast} />);
    expect(await screen.findByRole("status")).toHaveTextContent("浏览器已授权");

    act(() => { for (const listener of listeners) listener({ method: "browser/grantChanged", params: { sessionId: "other", active: false } }); });
    expect(screen.getByRole("status")).toBeInTheDocument();
    act(() => { for (const listener of listeners) listener({ method: "browser/grantChanged", params: { sessionId: "s1", active: false } }); });
    expect(screen.queryByRole("status")).toBeNull();
    act(() => { for (const listener of listeners) listener({ method: "browser/grantChanged", params: { sessionId: "s1", active: true } }); });

    await userEvent.click(screen.getByRole("button", { name: "撤销本会话的浏览器授权" }));
    expect(revokeBrowserAgentGrant).toHaveBeenCalledWith({ sessionId: "s1" });
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(onShowToast).toHaveBeenCalledWith(expect.stringContaining("已撤销"), "success");
  });

  it("names the user's own Chrome when the extension is in effect, and the Eidos browser after fallback", async () => {
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, target: "chrome_extension", effectiveTarget: "chrome_extension" }) });
    const { unmount } = render(<BrowserGrantIndicator sessionId="s1" onShowToast={vi.fn()} />);
    expect(await screen.findByRole("status")).toHaveTextContent("我的 Chrome 已授权");
    unmount();
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, target: "chrome_extension", effectiveTarget: "eidos" }) });
    render(<BrowserGrantIndicator sessionId="s1" onShowToast={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/^浏览器已授权/));
  });

  it("stays hidden without a grant", async () => {
    const readBrowserAgentStatus = vi.fn().mockResolvedValue({ ...status, granted: false });
    api({ readBrowserAgentStatus });
    render(<BrowserGrantIndicator sessionId="s1" onShowToast={vi.fn()} />);
    await waitFor(() => expect(readBrowserAgentStatus).toHaveBeenCalledWith({ sessionId: "s1" }));
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("Built-in browser plugin row", () => {
  const plugin = { id: "eidos-browser", name: "eidos-browser", description: "Browser tools", version: "1.0.0", installed: true };

  it("reports Chrome and resources and closes the shared browser", async () => {
    const closeBrowserAgent = vi.fn().mockResolvedValue({ closed: true });
    const onShowToast = vi.fn();
    api({
      builtinPlugins: vi.fn().mockResolvedValue({ plugins: [plugin] }),
      readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, running: true }),
      closeBrowserAgent,
    });
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={onShowToast} />);
    expect(await screen.findByText("使用 Eidos 浏览器 · Google Chrome 156.0 · 资源已就绪 · 浏览器运行中")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "关闭浏览器" }));
    expect(closeBrowserAgent).toHaveBeenCalledOnce();
    expect(onShowToast).toHaveBeenCalledWith(expect.stringContaining("已关闭 Eidos 浏览器"), "success");
    await waitFor(() => expect(screen.queryByRole("button", { name: "关闭浏览器" })).toBeNull());
  });

  it("asks for Google Chrome when it is missing", async () => {
    api({
      builtinPlugins: vi.fn().mockResolvedValue({ plugins: [plugin] }),
      readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, chromeInstalled: false, chromeVersion: null, resource: "preparing" }),
    });
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={vi.fn()} />);
    expect(await screen.findByText("使用 Eidos 浏览器 · 未找到 Google Chrome，请先安装 · 正在准备资源")).toBeInTheDocument();
  });
});

describe("Built-in browser sign-in and data", () => {
  const plugin = { id: "eidos-browser", name: "eidos-browser", description: "Browser tools", version: "1.0.0", installed: true };

  it("opens the Eidos browser for sign-in and clears its data only after confirmation", async () => {
    const openBrowserAgent = vi.fn().mockResolvedValue({ opened: true });
    const clearBrowserAgentProfile = vi.fn().mockResolvedValue({ cleared: true, revokedGrants: 2 });
    const onShowToast = vi.fn();
    api({
      builtinPlugins: vi.fn().mockResolvedValue({ plugins: [plugin] }),
      readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, running: false }),
      openBrowserAgent, clearBrowserAgentProfile,
    });
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={onShowToast} />);
    await userEvent.click(await screen.findByRole("button", { name: "打开浏览器登录" }));
    expect(openBrowserAgent).toHaveBeenCalledOnce();
    expect(await screen.findByRole("button", { name: "关闭浏览器" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "清除浏览器数据" }));
    const confirm = screen.getByRole("alertdialog", { name: "清除浏览器数据" });
    expect(confirm).toHaveTextContent("无法撤回");
    expect(clearBrowserAgentProfile).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "清除" }));
    expect(clearBrowserAgentProfile).toHaveBeenCalledOnce();
    await waitFor(() => expect(onShowToast).toHaveBeenLastCalledWith(expect.stringContaining("已清除"), "success"));
  });

  it("explains why the browser could not open", async () => {
    const onShowToast = vi.fn();
    api({
      builtinPlugins: vi.fn().mockResolvedValue({ plugins: [plugin] }),
      readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, running: false }),
      openBrowserAgent: vi.fn().mockResolvedValue({ opened: false, errorCode: "browser_profile_in_use" }),
    });
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={onShowToast} />);
    await userEvent.click(await screen.findByRole("button", { name: "打开浏览器登录" }));
    await waitFor(() => expect(onShowToast).toHaveBeenCalledWith(expect.stringContaining("另一个 Chrome 窗口"), "error"));
  });
});

describe("Choosing which Chrome the Agent uses", () => {
  const plugin = { id: "eidos-browser", name: "eidos-browser", description: "Browser tools", version: "1.0.0", installed: true };
  type Target = "eidos" | "user_chrome" | "chrome_extension";

  function runtime(initial: { target?: Target; configured?: boolean; presence?: "installed" | "missing" | "unknown" } = {}) {
    let target: Target = initial.target ?? "chrome_extension";
    let configured = initial.configured ?? false;
    const presence = initial.presence ?? "installed";
    const effective = () => target === "chrome_extension" && (!configured || presence === "missing") ? "eidos" : target;
    const calls = {
      setBrowserAgentTarget: vi.fn(async (request: { target: Target }) => { target = request.target; return { target, revokedGrants: 1 }; }),
      setBrowserAgentExtensionToken: vi.fn(async (request: { token: string }) => {
        if (!request.token.includes("TOKEN")) throw new Error("Error invoking remote method: BROWSER_EXTENSION_TOKEN_INVALID");
        configured = true;
        return { configured: true, revokedGrants: 0 };
      }),
      clearBrowserAgentExtensionToken: vi.fn(async () => { configured = false; return { configured: false, revokedGrants: 1 }; }),
      openBrowserExtensionStore: vi.fn(async () => undefined),
    };
    api({
      builtinPlugins: vi.fn().mockResolvedValue({ plugins: [plugin] }),
      readBrowserAgentStatus: vi.fn(async () => ({
        ...status, target, effectiveTarget: effective(), extensionConfigured: configured, extensionPresence: presence, attachAvailable: false,
      })),
      ...calls,
    });
    return calls;
  }

  it("recommends the tab-group mode and falls back to the Eidos browser until it is set up", async () => {
    runtime();
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={vi.fn()} />);
    const recommended = await screen.findByRole("radio", { name: "我的 Chrome（标签组）" });
    expect(recommended).toHaveAccessibleDescription(/Agent 只操作自己的标签组/);
    expect(recommended).toBeChecked();
    expect(screen.getByText("推荐")).toBeInTheDocument();
    expect(screen.getByText("设置完成前，新任务使用 Eidos 浏览器。")).toBeInTheDocument();
    expect(screen.getByText("已在 Chrome 中检测到扩展")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开 Chrome 应用商店 ↗" })).toBeNull();
    // The Eidos browser is still the one in use, so its controls stay available.
    expect(screen.getByRole("button", { name: "打开浏览器登录" })).toBeInTheDocument();
  });

  it("links to the Chrome Web Store when the extension is not found", async () => {
    const calls = runtime({ presence: "missing" });
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={vi.fn()} />);
    expect(await screen.findByText("未在 Chrome 中检测到扩展")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "打开 Chrome 应用商店 ↗" }));
    expect(calls.openBrowserExtensionStore).toHaveBeenCalledOnce();
  });

  it("saves the token only after a high-risk confirmation, then can remove it", async () => {
    const calls = runtime();
    const onShowToast = vi.fn();
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={onShowToast} />);
    const input = await screen.findByLabelText("Playwright Extension 令牌");
    expect(input).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请先粘贴令牌");
    await userEvent.type(input, "PLAYWRIGHT_MCP_EXTENSION_TOKEN=abc");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    const confirm = screen.getByRole("alertdialog", { name: "启用我的 Chrome" });
    expect(confirm).toHaveTextContent("不隔离账号");
    expect(confirm).toHaveTextContent("Cookie");
    expect(calls.setBrowserAgentExtensionToken).not.toHaveBeenCalled();
    await userEvent.click(within(confirm).getByRole("button", { name: "保存并启用" }));
    expect(calls.setBrowserAgentExtensionToken).toHaveBeenCalledWith({ token: "PLAYWRIGHT_MCP_EXTENSION_TOKEN=abc" });
    expect(await screen.findByText("令牌已保存")).toBeInTheDocument();
    expect(screen.getByText(/已启用。新任务会在你的 Chrome 中创建「Playwright · Eidos · 会话名」标签组/)).toBeInTheDocument();
    expect(screen.getByText(/使用你的 Chrome（标签组）/)).toBeInTheDocument();
    // Opening and clearing the Eidos browser no longer apply.
    expect(screen.queryByRole("button", { name: "打开浏览器登录" })).toBeNull();
    expect(screen.queryByRole("button", { name: "清除浏览器数据" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(calls.clearBrowserAgentExtensionToken).toHaveBeenCalledOnce();
    expect(await screen.findByText("设置完成前，新任务使用 Eidos 浏览器。")).toBeInTheDocument();
    expect(onShowToast).toHaveBeenLastCalledWith(expect.stringContaining("已移除令牌"), "success");
  });

  it("does not report a saved token as failed when only the status refresh fails", async () => {
    const calls = runtime();
    const read = window.eidosRuntime.readBrowserAgentStatus as ReturnType<typeof vi.fn>;
    const onShowToast = vi.fn();
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={onShowToast} />);
    await userEvent.type(await screen.findByLabelText("Playwright Extension 令牌"), "PLAYWRIGHT_MCP_EXTENSION_TOKEN=abc");
    read.mockRejectedValueOnce(new Error("Runtime busy"));
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await userEvent.click(within(screen.getByRole("alertdialog", { name: "启用我的 Chrome" })).getByRole("button", { name: "保存并启用" }));
    expect(calls.setBrowserAgentExtensionToken).toHaveBeenCalledOnce();
    await waitFor(() => expect(onShowToast).toHaveBeenCalledWith(expect.stringContaining("令牌已保存"), "success"));
    expect(screen.queryByText("令牌保存失败，请稍后重试。")).toBeNull();
  });

  it("explains an invalid token next to the field", async () => {
    runtime();
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={vi.fn()} />);
    await userEvent.type(await screen.findByLabelText("Playwright Extension 令牌"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await userEvent.click(within(screen.getByRole("alertdialog", { name: "启用我的 Chrome" })).getByRole("button", { name: "保存并启用" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("令牌格式不正确");
    expect(screen.queryByText("令牌已保存")).toBeNull();
  });

  it("uses remote debugging only after its own confirmation, and switches back by choice", async () => {
    const calls = runtime({ target: "eidos" });
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={vi.fn()} />);
    await userEvent.click(await screen.findByRole("radio", { name: "我的 Chrome（远程调试）" }));
    const confirm = screen.getByRole("alertdialog", { name: "使用我的 Chrome（远程调试）" });
    expect(confirm).toHaveTextContent("所有已登录的账号");
    // Choosing the option again is not a confirmation.
    await userEvent.click(screen.getByRole("radio", { name: "我的 Chrome（远程调试）" }));
    expect(calls.setBrowserAgentTarget).not.toHaveBeenCalled();
    await userEvent.click(within(confirm).getByRole("button", { name: "使用我的 Chrome" }));
    expect(calls.setBrowserAgentTarget).toHaveBeenCalledWith({ target: "user_chrome" });
    expect(await screen.findByText(/chrome:\/\/inspect\/#remote-debugging/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Eidos 浏览器" }));
    expect(calls.setBrowserAgentTarget).toHaveBeenLastCalledWith({ target: "eidos" });
    expect(screen.queryByLabelText("Playwright Extension 令牌")).toBeNull();
  });
});

describe("Browser resource retry", () => {
  const plugin = { id: "eidos-browser", name: "eidos-browser", description: "Browser tools", version: "1.0.0", installed: true };

  it("retries a failed browser resource download from the plugin row", async () => {
    const retryResource = vi.fn().mockResolvedValue({ accepted: true });
    const onShowToast = vi.fn();
    api({
      builtinPlugins: vi.fn().mockResolvedValue({ plugins: [plugin] }),
      readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, resource: "failed" }),
      retryResource,
    });
    render(<BuiltinPlugins onInstall={vi.fn()} onShowToast={onShowToast} />);
    await userEvent.click(await screen.findByRole("button", { name: "重试下载" }));
    expect(retryResource).toHaveBeenCalledWith({ packId: "browser" });
    expect(await screen.findByText(/正在准备资源/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试下载" })).toBeNull();
  });
});
