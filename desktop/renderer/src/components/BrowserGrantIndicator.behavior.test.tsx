import { act, render, screen, waitFor } from "@testing-library/react";
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
    expect(await screen.findByText("Google Chrome 156.0 · 资源已就绪 · 浏览器运行中")).toBeInTheDocument();
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
    expect(await screen.findByText("未找到 Google Chrome，请先安装 · 正在准备资源")).toBeInTheDocument();
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
