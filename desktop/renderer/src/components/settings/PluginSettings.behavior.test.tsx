import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PluginSettings } from "./PluginSettings.js";
import type { EidosRuntimeAPI, McpServerRecord, PluginRecord, SkillMetadata } from "../../contracts.js";
import type { ComponentProps } from "react";

const plugin: PluginRecord = {
  schemaVersion: 1, id: "demo", name: "Demo", version: "1.0.0", description: "Local checks",
  contentHash: "a".repeat(64), enabled: false, status: "installed", installedAt: 1, updatedAt: 1,
  compatibility: { format: "codex", declaredVersion: "1.0.0", issues: [] },
  hookReview: { contentHash: "a".repeat(64), trusted: false, executableCount: 1,
    installedRoot: "/plugins/demo", dataDirectory: "/plugins/demo/hook-data",
    definitions: [{ key: "stop", event: "Stop", command: "python3 check.py", handlerType: "command" }] },
};

const ownSkill: SkillMetadata = {
  schemaVersion: 1, qualifiedId: "demo:review", name: "Review", description: "Review local files", sourceKind: "plugin",
  pluginId: "demo", pluginVersion: "1.0.0", pluginHash: plugin.contentHash, contentHash: "b".repeat(64), enabled: true, available: true,
};
const server: McpServerRecord = {
  schemaVersion: 1, pluginId: "demo", pluginVersion: "1.0.0", pluginHash: plugin.contentHash,
  serverId: "local-tools", executable: "python3", argv: ["tools.py"], envNames: ["ACCESS_TOKEN"],
  permissionProfile: "workspace_read", startupTimeoutSeconds: 5, toolTimeoutSeconds: 10,
  declaredEnabled: true, consented: false, available: false, updatedAt: 1,
};
const runtimeDescriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");

function setup(value = plugin, overrides: Partial<ComponentProps<typeof PluginSettings>> = {}) {
  const props = { plugins: [value], pendingAction: undefined, onImportPlugin: vi.fn().mockResolvedValue(undefined),
    onTogglePlugin: vi.fn().mockResolvedValue(undefined), onTrustPluginHooks: vi.fn().mockResolvedValue(undefined),
    onRemovePlugin: vi.fn().mockResolvedValue(undefined), onShowToast: vi.fn(), ...overrides };
  render(<PluginSettings {...props} />);
  return props;
}

afterEach(() => {
  cleanup(); vi.restoreAllMocks();
  if (runtimeDescriptor) Object.defineProperty(window, "eidosRuntime", runtimeDescriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

describe("Plugin Hook review", () => {
  it("reviews commands before enabling and binds trust to the displayed package", async () => {
    const props = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "启用或停用 Demo" }));
    expect(props.onTogglePlugin).not.toHaveBeenCalled();
    expect(screen.getByText("python3 check.py")).toBeTruthy();
    expect(screen.getByText("/plugins/demo/hook-data")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "信任此版本的 Hook" }));
    await waitFor(() => expect(props.onTogglePlugin).toHaveBeenCalledWith("demo", true));
    expect(props.onTrustPluginHooks).toHaveBeenCalledWith("demo", plugin.contentHash, true);
  });

  it("cancel leaves the plugin untrusted and disabled", async () => {
    const props = setup();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "审阅 Hook" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(props.onTrustPluginHooks).not.toHaveBeenCalled();
    expect(props.onTogglePlugin).not.toHaveBeenCalled();
  });

  it("a failed trust request does not enable the plugin", async () => {
    const props = setup();
    props.onTrustPluginHooks.mockRejectedValue(new Error("插件版本已改变"));
    const user = userEvent.setup();
    await user.click(screen.getByRole("switch", { name: "启用或停用 Demo" }));
    await user.click(screen.getByRole("button", { name: "信任此版本的 Hook" }));
    await waitFor(() => expect(screen.getAllByText("插件版本已改变").length).toBeGreaterThan(0));
    expect(props.onTogglePlugin).not.toHaveBeenCalled();
  });

  it("keyboard review focuses cancel and Escape returns to the trigger", async () => {
    const props = setup();
    const user = userEvent.setup();
    const review = screen.getByRole("button", { name: "审阅 Hook" });
    review.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "取消" })));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "信任此版本的 Hook" }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(document.activeElement).toBe(review));
    expect(props.onTrustPluginHooks).not.toHaveBeenCalled();
  });
});

describe("Plugin resources", () => {
  it("shows packaged setup instructions without enabling capabilities", async () => {
    const readSkillDetail = vi.fn().mockResolvedValue({ qualifiedId: ownSkill.qualifiedId, content: "Setup guide", body: "Setup guide", directory: "/plugins/demo/skills/review" });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readSkillDetail } });
    const props = setup({ ...plugin, presentation: { developerName: "Example Team", license: "MIT", onboardingSkillId: ownSkill.qualifiedId } }, { skills: [ownSkill] });
    const user = userEvent.setup();
    expect(screen.queryByText("Example Team")).not.toBeInTheDocument();
    expect(screen.queryByText(/个技能/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看 Demo 的资源" }));
    await user.click(screen.getByRole("button", { name: /Review/ }));
    expect(await screen.findByText("Setup guide")).toBeInTheDocument();
    expect(props.onTogglePlugin).not.toHaveBeenCalled();
    expect(props.onTrustPluginHooks).not.toHaveBeenCalled();
  });

  it("identifies a version update and reminds the user to review authorization", async () => {
    const props = setup(plugin, { onImportPlugin: vi.fn().mockResolvedValue({ ...plugin, version: "2.0.0" }) });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "更多插件选项" }));
    await user.click(screen.getByRole("menuitem", { name: "导入本地 Plugin" }));
    await waitFor(() => expect(props.onShowToast).toHaveBeenCalledWith("Plugin 已更新至 v2.0.0，请重新启用并审阅授权。", "success"));
    expect(props.onTogglePlugin).not.toHaveBeenCalled();
  });

  it("shows only the selected plugin resources and keeps skill controls inside the plugin", async () => {
    const readSkillDetail = vi.fn().mockResolvedValue({ qualifiedId: ownSkill.qualifiedId, content: "Review instructions", body: "Review instructions", directory: "/plugins/demo/skills/review" });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readSkillDetail } });
    const onToggleSkill = vi.fn().mockResolvedValue(undefined);
    setup(plugin, { skills: [ownSkill, { ...ownSkill, name: "Other skill", pluginId: "other", qualifiedId: "other:review" }], mcpServers: [server, { ...server, pluginId: "other", serverId: "foreign-tools" }], onToggleSkill });
    const user = userEvent.setup();
    const trigger = screen.getByRole("button", { name: "查看 Demo 的资源" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Demo" });
    expect(within(dialog).getByText("Review")).toBeInTheDocument();
    expect(within(dialog).getByText("local-tools")).toBeInTheDocument();
    expect(within(dialog).getByText("Stop · command")).toBeInTheDocument();
    expect(within(dialog).queryByText("Other skill")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("foreign-tools")).not.toBeInTheDocument();
    expect(readSkillDetail).not.toHaveBeenCalled();
    const skillButton = within(dialog).getByRole("button", { name: /Review.*已开启/ });
    await user.click(skillButton);
    expect(await screen.findByText("Review instructions")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "卸载" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: "启用或禁用 Review" }));
    expect(onToggleSkill).toHaveBeenCalledWith(ownSkill.qualifiedId, false);
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Demo" })).toBeInTheDocument();
    await waitFor(() => expect(skillButton).toHaveFocus());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("disables the nested plugin-skill switch while that skill is updating", async () => {
    const readSkillDetail = vi.fn().mockResolvedValue({
      qualifiedId: ownSkill.qualifiedId,
      content: "Review instructions",
      body: "Review instructions",
      directory: "/plugins/demo/skills/review",
    });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readSkillDetail } });
    setup(plugin, {
      skills: [ownSkill],
      pendingSkillIds: new Set([ownSkill.qualifiedId]),
      onToggleSkill: vi.fn().mockResolvedValue(undefined),
    });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "查看 Demo 的资源" }));
    await user.click(screen.getByRole("button", { name: /Review.*已开启/ }));
    expect(await screen.findByRole("switch", { name: "启用或禁用 Review" })).toBeDisabled();
  });

  it("requires MCP review, preserves errors, and never grants consent from opening details", async () => {
    const onToggleMcp = vi.fn().mockRejectedValueOnce(new Error("授权保存失败")).mockResolvedValue(undefined);
    const readMcpTools = vi.fn().mockResolvedValue({ state: "not_authorized", tools: [] });
    Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readMcpTools } as unknown as EidosRuntimeAPI });
    setup({ ...plugin, enabled: true }, { mcpServers: [server], onToggleMcp });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "查看 Demo 的资源" }));
    await user.click(screen.getByRole("button", { name: "查看 local-tools 的工具" }));
    await waitFor(() => expect(readMcpTools).toHaveBeenCalled());
    expect(onToggleMcp).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "启用" }));
    const confirmation = screen.getByRole("alertdialog");
    expect(confirmation).toHaveTextContent("python3 tools.py");
    expect(confirmation).toHaveTextContent("workspace_read");
    expect(confirmation).toHaveTextContent("ACCESS_TOKEN");
    await user.click(within(confirmation).getByRole("button", { name: "取消" }));
    expect(onToggleMcp).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "启用" }));
    await user.click(screen.getByRole("button", { name: "授权并启用" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("操作失败，请查看 Runtime 日志。");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "授权并启用" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onToggleMcp).toHaveBeenLastCalledWith("demo", "local-tools", true);
    expect(screen.getByRole("dialog", { name: "Demo" })).toBeInTheDocument();
  });

  it("shows compatibility and empty resources without claiming that an App-only plugin is usable", async () => {
    setup({ ...plugin, hookReview: undefined, compatibility: { format: "codex", issues: ["codex_apps_not_supported"] } });
    await userEvent.setup().click(screen.getByRole("button", { name: "查看 Demo 的资源" }));
    const dialog = screen.getByRole("dialog", { name: "Demo" });
    expect(dialog).toHaveTextContent("当前版本尚未接入此插件的 Codex App。");
    expect(dialog).toHaveTextContent("插件未提供 Skill。");
    expect(dialog).toHaveTextContent("插件未提供 Hook。");
  });

  it("keeps MCP consent pending until the request completes and prevents duplicate submissions", async () => {
    let complete!: () => void;
    const onToggleMcp = vi.fn().mockImplementation(() => new Promise<void>(resolve => { complete = resolve; }));
    setup({ ...plugin, enabled: true }, { mcpServers: [server], onToggleMcp });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "查看 Demo 的资源" }));
    await user.click(screen.getByRole("button", { name: "启用" }));
    await user.click(screen.getByRole("button", { name: "授权并启用" }));
    expect(screen.getByRole("button", { name: "授权并启用" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "授权并启用" }));
    expect(onToggleMcp).toHaveBeenCalledTimes(1);
    complete();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByRole("dialog", { name: "Demo" })).toBeInTheDocument();
  });

  it("keeps disabled plugin resources inspectable without silently enabling the plugin", async () => {
    const onToggleMcp = vi.fn();
    const props = setup(plugin, { skills: [{ ...ownSkill, available: false }], mcpServers: [server], onToggleMcp });
    await userEvent.setup().click(screen.getByRole("button", { name: "查看 Demo 的资源" }));
    const dialog = screen.getByRole("dialog", { name: "Demo" });
    expect(dialog).toHaveTextContent("Review");
    expect(within(dialog).getByRole("button", { name: "启用" })).toBeDisabled();
    expect(props.onTogglePlugin).not.toHaveBeenCalled();
    expect(onToggleMcp).not.toHaveBeenCalled();
  });

  it("restores focus to import when the selected plugin is removed by a refreshed snapshot", async () => {
    const props = { plugins: [plugin], pendingAction: undefined, onImportPlugin: vi.fn(), onTogglePlugin: vi.fn(), onRemovePlugin: vi.fn(), onShowToast: vi.fn() };
    const view = render(<PluginSettings {...props} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "查看 Demo 的资源" }));
    view.rerender(<PluginSettings {...props} plugins={[]} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "插件市场" })).toHaveFocus());
  });

  it("opens plugin marketplace dialog, lists available builtin plugins, and handles install", async () => {
    const builtinPlugin = { id: "eidos-github", name: "GitHub", description: "GitHub tools", version: "1.0.0", installed: false };
    const onInstallBuiltinPlugin = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        builtinPlugins: vi.fn().mockResolvedValue({ plugins: [builtinPlugin] }),
      },
    });
    setup(plugin, { onInstallBuiltinPlugin });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "插件市场" }));
    const dialog = await screen.findByRole("dialog", { name: "插件市场" });
    expect(dialog).toBeInTheDocument();
    const closeIcon = within(dialog).getByRole("button", { name: "关闭插件市场" });
    await waitFor(() => expect(closeIcon).toHaveFocus());
    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "关闭" })).toHaveFocus();
    await user.tab();
    expect(closeIcon).toHaveFocus();
    expect(within(dialog).getByText("GitHub")).toBeInTheDocument();
    expect(within(dialog).getByText("v1.0.0")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "安装" }));
    expect(onInstallBuiltinPlugin).toHaveBeenCalledWith("eidos-github");
    await user.click(within(dialog).getByRole("button", { name: "关闭插件市场" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens marketplace from empty state when no plugins are installed", async () => {
    Object.defineProperty(window, "eidosRuntime", {
      configurable: true,
      value: {
        builtinPlugins: vi.fn().mockResolvedValue({ plugins: [] }),
      },
    });
    const props = { plugins: [], pendingAction: undefined, onImportPlugin: vi.fn(), onTogglePlugin: vi.fn(), onRemovePlugin: vi.fn(), onShowToast: vi.fn() };
    render(<PluginSettings {...props} />);
    const user = userEvent.setup();
    expect(screen.getByText("尚未导入任何插件")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "浏览插件市场" }));
    expect(await screen.findByRole("dialog", { name: "插件市场" })).toBeInTheDocument();
  });
});
