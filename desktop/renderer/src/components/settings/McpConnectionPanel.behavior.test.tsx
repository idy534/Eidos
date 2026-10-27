import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { McpConnectionPanel } from "./McpConnectionPanel.js";
import { BuiltinPlugins } from "./BuiltinPlugins.js";
import type { McpServerRecord } from "../../contracts.js";

const server: McpServerRecord = {
  schemaVersion: 1, pluginId: "eidos-github", pluginVersion: "1.0.0", pluginHash: "a".repeat(64),
  serverId: "github", executable: "", argv: [], envNames: [], permissionProfile: "connector",
  startupTimeoutSeconds: 15, toolTimeoutSeconds: 60, declaredEnabled: true, consented: false, available: false, updatedAt: 0,
  remote: { url: "https://api.githubcopilot.com/mcp/", authType: "github-device" },
};
const initial = { pluginId: "eidos-github", serverId: "github", status: "disconnected", authType: "github-device", browserAvailable: false, updatedAt: 0 };
const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
afterEach(() => { cleanup(); if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor); vi.restoreAllMocks(); });
function api(value: object) { Object.defineProperty(window, "eidosRuntime", { configurable: true, value }); }

describe("Remote MCP accounts", () => {
  it("connects with a password field, clears the token, and disconnects the account", async () => {
    const mcpConnection = vi.fn().mockResolvedValueOnce(initial)
      .mockResolvedValueOnce({ ...initial, status: "connected", accountLabel: "octocat" })
      .mockResolvedValueOnce(initial);
    const openMcpLogin = vi.fn().mockResolvedValue(undefined);
    const onChanged = vi.fn().mockResolvedValue(undefined);
    api({ mcpConnection, openMcpLogin });
    render(<McpConnectionPanel server={server} enabled onChanged={onChanged} />);
    const user = userEvent.setup();
    const input = await screen.findByLabelText("个人访问令牌");
    expect(input).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "显示令牌" }));
    expect(input).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "隐藏令牌" }));
    expect(input).toHaveAttribute("type", "password");
    expect(screen.queryByRole("button", { name: "在浏览器中登录" })).not.toBeInTheDocument();
    expect(screen.queryByText(/当前版本尚未配置 GitHub 浏览器登录/)).not.toBeInTheDocument();
    expect(screen.queryByText(/账号连接后，任务可以使用此服务/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "创建 GitHub 令牌" }));
    expect(openMcpLogin).toHaveBeenCalledWith(expect.objectContaining({ method: "token" }));
    await user.type(input, "fixture-token");
    await user.click(screen.getByRole("button", { name: "使用令牌连接" }));
    expect(mcpConnection).toHaveBeenCalledWith(expect.objectContaining({ token: "fixture-token", method: "token", action: "connect" }));
    expect(await screen.findByText("账号已连接 · octocat")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "断开账号连接" }));
    expect(await screen.findByLabelText("个人访问令牌")).toHaveValue("");
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("opens the browser after device login starts and allows cancellation", async () => {
    const mcpConnection = vi.fn().mockResolvedValueOnce({ ...initial, browserAvailable: true })
      .mockResolvedValueOnce({ ...initial, status: "connecting", browserAvailable: true,
        authorizationUrl: "https://github.com/login/device", userCode: "ABCD-EFGH" })
      .mockResolvedValueOnce(initial);
    const openMcpLogin = vi.fn().mockResolvedValue(undefined);
    api({ mcpConnection, openMcpLogin });
    render(<McpConnectionPanel server={server} enabled />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "在浏览器中登录" }));
    expect(await screen.findByText("ABCD-EFGH")).toBeInTheDocument();
    await waitFor(() => expect(openMcpLogin).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "取消登录" }));
    expect(mcpConnection).toHaveBeenLastCalledWith(expect.objectContaining({ action: "cancel" }));
    expect(screen.queryByText("ABCD-EFGH")).not.toBeInTheDocument();
  });

  it("keeps controls disabled until the plugin is enabled and shows connection errors", async () => {
    api({ mcpConnection: vi.fn().mockResolvedValue({ ...initial, status: "expired", errorCode: "mcp_login_required" }) });
    render(<McpConnectionPanel server={server} enabled={false} />);
    expect(await screen.findByText("凭据已失效，请重新连接账号。")).toBeInTheDocument();
    expect(screen.getByLabelText("个人访问令牌")).toBeDisabled();
  });

  it("ignores a late connection failure after switching the plugin version", async () => {
    let rejectConnect!: (error: Error) => void;
    const pending = new Promise<never>((_resolve, reject) => { rejectConnect = reject; });
    const mcpConnection = vi.fn().mockResolvedValueOnce(initial).mockReturnValueOnce(pending)
      .mockResolvedValueOnce({ ...initial, status: "connected", accountLabel: "current-account" });
    api({ mcpConnection });
    const view = render(<McpConnectionPanel server={server} enabled />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("个人访问令牌"), "fixture-token");
    await user.click(screen.getByRole("button", { name: "使用令牌连接" }));
    view.rerender(<McpConnectionPanel server={{ ...server, pluginHash: "b".repeat(64) }} enabled />);
    expect(await screen.findByText("账号已连接 · current-account")).toBeInTheDocument();
    await act(async () => rejectConnect(new Error("old operation failed")));
    expect(screen.queryByText("连接操作失败，请稍后重试。")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "断开账号连接" })).toBeEnabled();
  });

  it("lists and installs a bundled plugin through the shared catalog", async () => {
    const plugin = { id: "eidos-github", name: "GitHub", description: "GitHub tools", version: "1.0.0", installed: false };
    api({ builtinPlugins: vi.fn().mockResolvedValueOnce({ plugins: [plugin] }).mockResolvedValueOnce({ plugins: [{ ...plugin, installed: true }] }) });
    const onInstall = vi.fn().mockResolvedValue(undefined);
    render(<BuiltinPlugins onInstall={onInstall} onShowToast={vi.fn()} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "安装" }));
    expect(onInstall).toHaveBeenCalledWith("eidos-github");
    expect(await screen.findByRole("button", { name: "已安装" })).toBeDisabled();
  });

  it("triggers floating toast notification instead of inline error when installation fails", async () => {
    const plugin = { id: "eidos-github", name: "GitHub", description: "GitHub tools", version: "1.0.0", installed: false };
    api({ builtinPlugins: vi.fn().mockResolvedValue({ plugins: [plugin] }) });
    const onInstall = vi.fn().mockRejectedValue(new Error("install failed"));
    const onShowToast = vi.fn();
    render(<BuiltinPlugins onInstall={onInstall} onShowToast={onShowToast} />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "安装" }));
    expect(onInstall).toHaveBeenCalledWith("eidos-github");
    expect(onShowToast).toHaveBeenCalledWith("安装 GitHub 插件失败，请稍后重试。", "error");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
