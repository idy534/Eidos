import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserSettingsSection } from "./BrowserSettingsSection.js";

type Target = "eidos" | "user_chrome" | "chrome_extension";
const status = {
  chromeInstalled: true, chromeVersion: "156.0", running: false, pluginInstalled: true, pluginEnabled: true,
  resource: "ready" as const, granted: true,
};
const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

function api(value: object) {
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value });
}

afterEach(() => {
  vi.restoreAllMocks();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

function runtime(initial: { target?: Target; configured?: boolean; presence?: "installed" | "missing" | "unknown" } = {}) {
  let target: Target = initial.target ?? "chrome_extension";
  let configured = initial.configured ?? false;
  const presence = initial.presence ?? "installed";
  const effective = () => target === "chrome_extension" && (!configured || presence === "missing") ? "eidos" : target;
  const calls = {
    readBrowserAgentStatus: vi.fn(async () => ({
      ...status, target, effectiveTarget: effective(), extensionConfigured: configured, extensionPresence: presence, attachAvailable: false,
    })),
    setBrowserAgentTarget: vi.fn(async (request: { target: Target }) => { target = request.target; return { target, revokedGrants: 1 }; }),
    setBrowserAgentExtensionToken: vi.fn(async (request: { token: string }) => {
      if (!request.token.includes("TOKEN")) throw new Error("Error invoking remote method: BROWSER_EXTENSION_TOKEN_INVALID");
      configured = true;
      return { configured: true, revokedGrants: 0 };
    }),
    clearBrowserAgentExtensionToken: vi.fn(async () => { configured = false; return { configured: false, revokedGrants: 1 }; }),
    openBrowserExtensionStore: vi.fn(async () => undefined),
  };
  api(calls);
  return calls;
}

describe("Browser settings", () => {
  it("defaults to the tab-group mode and says the Eidos browser is used until it is set up", async () => {
    runtime();
    render(<BrowserSettingsSection onShowToast={vi.fn()} />);
    expect(await screen.findByRole("combobox", { name: "Agent 使用的浏览器" })).toHaveValue("chrome_extension");
    expect(screen.getByText("完成下方设置前，使用 Eidos 浏览器")).toBeInTheDocument();
    expect(screen.getByText("已安装")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Chrome 应用商店 ↗" })).toBeNull();
    // Opening or clearing the Eidos browser from Settings was removed.
    expect(screen.queryByRole("button", { name: "打开浏览器登录" })).toBeNull();
    expect(screen.queryByRole("button", { name: "清除浏览器数据" })).toBeNull();
  });

  it("does not claim the extension is missing when Chrome's profile cannot be read", async () => {
    const calls = runtime({ presence: "unknown" });
    render(<BrowserSettingsSection onShowToast={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Chrome 应用商店 ↗" }));
    expect(calls.openBrowserExtensionStore).toHaveBeenCalledOnce();
    expect(screen.queryByText(/无法读取/)).toBeNull();
    expect(screen.queryByText("已安装")).toBeNull();
  });

  it("saves the token only after a modal confirmation, then can remove it", async () => {
    const calls = runtime();
    const onShowToast = vi.fn();
    const onDialogChange = vi.fn();
    render(<BrowserSettingsSection onShowToast={onShowToast} onDialogChange={onDialogChange} />);
    const input = await screen.findByLabelText("Playwright Extension 令牌");
    expect(input).toHaveAttribute("type", "password");
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    await userEvent.type(input, "PLAYWRIGHT_MCP_EXTENSION_TOKEN=abc{Enter}");
    const confirm = screen.getByRole("alertdialog", { name: "使用你的 Chrome" });
    expect(confirm).toHaveTextContent("不隔离账号");
    expect(confirm).toHaveTextContent("Cookie");
    expect(onDialogChange).toHaveBeenLastCalledWith(true);
    expect(calls.setBrowserAgentExtensionToken).not.toHaveBeenCalled();
    await userEvent.click(within(confirm).getByRole("button", { name: "保存" }));
    expect(onDialogChange).toHaveBeenLastCalledWith(false);
    expect(calls.setBrowserAgentExtensionToken).toHaveBeenCalledWith({ token: "PLAYWRIGHT_MCP_EXTENSION_TOKEN=abc" });
    expect(await screen.findByText("已保存")).toBeInTheDocument();
    expect(screen.getByText("Agent 在你的 Chrome 中使用自己的标签组")).toBeInTheDocument();
    expect(onShowToast).toHaveBeenLastCalledWith("令牌已保存，新任务将使用你的 Chrome", "success");

    await userEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(calls.clearBrowserAgentExtensionToken).toHaveBeenCalledOnce();
    expect(await screen.findByText("完成下方设置前，使用 Eidos 浏览器")).toBeInTheDocument();
    expect(onShowToast).toHaveBeenLastCalledWith("令牌已移除，新任务将使用 Eidos 浏览器", "success");
  });

  it("reports an invalid token as a notification, not inline", async () => {
    runtime();
    const onShowToast = vi.fn();
    render(<BrowserSettingsSection onShowToast={onShowToast} />);
    await userEvent.type(await screen.findByLabelText("Playwright Extension 令牌"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await userEvent.click(within(screen.getByRole("alertdialog", { name: "使用你的 Chrome" })).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(onShowToast).toHaveBeenCalledWith(expect.stringContaining("令牌格式不正确"), "error"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("已保存")).toBeNull();
  });

  it("does not report a saved token as failed when only the status refresh fails", async () => {
    const calls = runtime();
    const onShowToast = vi.fn();
    render(<BrowserSettingsSection onShowToast={onShowToast} />);
    await userEvent.type(await screen.findByLabelText("Playwright Extension 令牌"), "PLAYWRIGHT_MCP_EXTENSION_TOKEN=abc");
    calls.readBrowserAgentStatus.mockRejectedValueOnce(new Error("Runtime busy"));
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await userEvent.click(within(screen.getByRole("alertdialog", { name: "使用你的 Chrome" })).getByRole("button", { name: "保存" }));
    expect(calls.setBrowserAgentExtensionToken).toHaveBeenCalledOnce();
    await waitFor(() => expect(onShowToast).toHaveBeenCalledWith("令牌已保存，新任务将使用你的 Chrome", "success"));
    expect(onShowToast).not.toHaveBeenCalledWith(expect.stringContaining("失败"), "error");
  });

  it("switches to remote debugging only after its confirmation, and back by choice", async () => {
    const calls = runtime({ target: "eidos" });
    const onShowToast = vi.fn();
    render(<BrowserSettingsSection onShowToast={onShowToast} />);
    const select = await screen.findByRole("combobox", { name: "Agent 使用的浏览器" });
    expect(screen.queryByLabelText("Playwright Extension 令牌")).toBeNull();
    await userEvent.selectOptions(select, "user_chrome");
    const confirm = screen.getByRole("alertdialog", { name: "使用远程调试" });
    expect(confirm).toHaveTextContent("全部标签页");
    expect(select).toHaveValue("eidos");
    await userEvent.click(within(confirm).getByRole("button", { name: "取消" }));
    expect(calls.setBrowserAgentTarget).not.toHaveBeenCalled();

    await userEvent.selectOptions(select, "user_chrome");
    await userEvent.click(within(screen.getByRole("alertdialog", { name: "使用远程调试" })).getByRole("button", { name: "切换" }));
    expect(calls.setBrowserAgentTarget).toHaveBeenCalledWith({ target: "user_chrome" });
    expect(await screen.findByText(/chrome:\/\/inspect\/#remote-debugging/)).toBeInTheDocument();
    expect(onShowToast).toHaveBeenLastCalledWith("已切换到你的 Chrome（远程调试），各会话的浏览器授权已撤销", "success");
    await userEvent.selectOptions(select, "eidos");
    expect(calls.setBrowserAgentTarget).toHaveBeenLastCalledWith({ target: "eidos" });
  });
});

describe("Browser readiness", () => {
  it("offers a retry when the settings cannot be read", async () => {
    const readBrowserAgentStatus = vi.fn().mockRejectedValueOnce(new Error("Runtime busy")).mockResolvedValue(status);
    api({ readBrowserAgentStatus });
    render(<BrowserSettingsSection onShowToast={vi.fn()} />);
    expect(await screen.findByText("无法读取浏览器设置")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("combobox", { name: "Agent 使用的浏览器" })).toBeInTheDocument();
    expect(readBrowserAgentStatus).toHaveBeenCalledTimes(2);
  });

  it("only mentions Chrome and resources when something needs attention", async () => {
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, chromeInstalled: false, chromeVersion: null }) });
    const view = render(<BrowserSettingsSection onShowToast={vi.fn()} />);
    expect(await screen.findByText("未找到，请先安装 Google Chrome")).toBeInTheDocument();
    expect(screen.queryByText("浏览器资源")).toBeNull();
    view.unmount();

    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue(status) });
    render(<BrowserSettingsSection onShowToast={vi.fn()} />);
    await screen.findByRole("combobox", { name: "Agent 使用的浏览器" });
    expect(screen.queryByText("Google Chrome")).toBeNull();
    expect(screen.queryByText("浏览器资源")).toBeNull();
  });

  it("retries a failed browser resource download", async () => {
    const retryResource = vi.fn().mockResolvedValue({ accepted: true });
    const onShowToast = vi.fn();
    api({ readBrowserAgentStatus: vi.fn().mockResolvedValue({ ...status, resource: "failed" }), retryResource });
    render(<BrowserSettingsSection onShowToast={onShowToast} />);
    expect(await screen.findByText("下载失败")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retryResource).toHaveBeenCalledWith({ packId: "browser" });
    expect(await screen.findByText("正在准备")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });
});
