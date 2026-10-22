import fs from "node:fs";
import path from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { McpServerRecord } from "../../contracts.js";
import type { McpToolDetail, McpToolsRequest, McpToolsResponse } from "../../../../shared/mcp-details.js";
import { McpSettings } from "./McpSettings.js";

const descriptor = Object.getOwnPropertyDescriptor(window, "eidosRuntime");
const server: McpServerRecord = {
  schemaVersion: 1, pluginId: "manual", pluginVersion: "manual", pluginHash: "abc", serverId: "playwright",
  executable: "npx", argv: ["@playwright/mcp@latest"], envNames: [], permissionProfile: "connector",
  startupTimeoutSeconds: 15, toolTimeoutSeconds: 60, declaredEnabled: true, consented: true, available: true, updatedAt: 1,
};
const catalogId = "00000000-0000-4000-8000-000000000001";
const tools: McpToolDetail[] = Array.from({ length: 23 }, (_, index) => ({
  name: `tool_${index.toString().padStart(2, "0")}`, description: `工具说明 ${index}`,
  inputSchema: { type: "object", properties: { url: { type: "string", description: "目标网页地址" } }, required: ["url"] },
}));
function response(request: McpToolsRequest): McpToolsResponse {
  const filtered = tools.filter(tool => `${tool.name} ${tool.description}`.includes(request.query || ""));
  const offset = Number(request.cursor?.split(":")[1] ?? 0);
  return { state: "ready", description: "网页自动化工具", tools: filtered.slice(offset, offset + 10),
    total: filtered.length, catalogId, fetchedAt: 1792627200000,
    nextCursor: offset + 10 < filtered.length ? `${catalogId}:${offset + 10}` : null };
}
function setup(api = vi.fn<(request: McpToolsRequest) => Promise<McpToolsResponse>>().mockImplementation(async request => response(request)), record = server) {
  Object.defineProperty(window, "eidosRuntime", { configurable: true, value: { readMcpTools: api } });
  const props = { servers: [record], pendingAction: null, onToggleMcp: vi.fn(), onCreateMcp: vi.fn(),
    onUpdateMcp: vi.fn(), onRemoveMcp: vi.fn(), onShowToast: vi.fn() };
  const view = render(<McpSettings {...props} />);
  return { api, props, ...view };
}
function scrollNearBottom() {
  const list = screen.getByRole("region", { name: "MCP 工具列表" });
  Object.defineProperties(list, { clientHeight: { configurable: true, value: 400 }, scrollHeight: { configurable: true, value: 1200 }, scrollTop: { configurable: true, value: 750, writable: true } });
  fireEvent.scroll(list);
}
afterEach(() => {
  vi.restoreAllMocks();
  if (descriptor) Object.defineProperty(window, "eidosRuntime", descriptor);
  else delete (window as Partial<Window>).eidosRuntime;
});

it("opens the accordion from the name, shows server description and restores focus on Escape", async () => {
  const user = userEvent.setup(); const { api } = setup();
  const trigger = screen.getByRole("button", { name: "查看 playwright 工具" });
  trigger.focus(); await user.keyboard("{Enter}");
  const dialog = await screen.findByRole("dialog", { name: "playwright" });
  expect(await screen.findByText("网页自动化工具")).toBeInTheDocument();
  expect(dialog).not.toHaveTextContent("MCP 工具详情");
  expect(screen.queryByRole("button", { name: "完成" })).not.toBeInTheDocument();
  expect(screen.queryByText("tool_10")).not.toBeInTheDocument();
  await user.click(screen.getByText("tool_00"));
  expect(screen.getByText("tool_00").closest("details")).toHaveAttribute("open");
  expect(screen.getAllByText("目标网页地址")[0]).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(api).toHaveBeenCalledTimes(1);
});

it("opens the dialog when clicking anywhere on the card main row, but not on actions", async () => {
  const user = userEvent.setup();
  const { container } = setup();
  const mainRow = container.querySelector(".mcp-card__main")!;
  const avatar = container.querySelector(".mcp-card__avatar")!;

  // Click avatar icon opens dialog
  await user.click(avatar);
  expect(await screen.findByRole("dialog", { name: "playwright" })).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  // Click main row opens dialog
  await user.click(mainRow);
  expect(await screen.findByRole("dialog", { name: "playwright" })).toBeInTheDocument();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  // Click edit button does not open tool detail dialog
  await user.click(screen.getByRole("button", { name: "编辑" }));
  expect(screen.queryByRole("dialog", { name: "playwright" })).not.toBeInTheDocument();
});

it.each([null, "   "])("uses the command when the server has no description (%s)", async description => {
  setup(vi.fn().mockResolvedValue({ ...response({ pluginId: "manual", serverId: "playwright" }), description }));
  await userEvent.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await screen.findByText("tool_00");
  expect(screen.getByRole("dialog").querySelector(".mcp-detail-command")).toHaveTextContent("$ npx @playwright/mcp@latest");
});

it("loads ten at a time without duplicate requests and retains expanded rows", async () => {
  const { api } = setup(); const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await user.click(await screen.findByText("tool_00"));
  let resolve!: (value: McpToolsResponse) => void;
  api.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  scrollNearBottom(); scrollNearBottom();
  expect(api).toHaveBeenCalledTimes(2);
  expect(screen.getByText("正在加载更多工具…")).toBeInTheDocument();
  await act(async () => resolve(response({ pluginId: "manual", serverId: "playwright", cursor: `${catalogId}:10` })));
  expect(await screen.findByText("tool_19")).toBeInTheDocument();
  expect(screen.getByText("tool_00").closest("details")).toHaveAttribute("open");
  scrollNearBottom(); await screen.findByText("tool_22");
  expect(screen.getByText("已全部加载")).toBeInTheDocument();
  scrollNearBottom(); expect(api).toHaveBeenCalledTimes(3);
});

it("searches tools outside the first page and clears search using the same catalog", async () => {
  const { api } = setup(); const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await screen.findByText("tool_00");
  await user.type(screen.getByRole("searchbox", { name: "搜索工具名称或描述" }), "说明 22");
  expect(await screen.findByText("tool_22")).toBeInTheDocument();
  expect(api).toHaveBeenLastCalledWith({ pluginId: "manual", serverId: "playwright", query: "说明 22", cursor: `${catalogId}:0` });
  expect(screen.queryByText("tool_00")).not.toBeInTheDocument();
  expect(screen.getByText("网页自动化工具")).toBeInTheDocument();
  await user.clear(screen.getByRole("searchbox", { name: "搜索工具名称或描述" }));
  await screen.findByText("tool_00");
});

it("keeps loaded rows after a page failure and retries the same page", async () => {
  const { api } = setup(); const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await screen.findByText("tool_00");
  api.mockRejectedValueOnce(new Error("transport"));
  scrollNearBottom(); await screen.findByRole("alert");
  expect(screen.getByText("tool_09")).toBeInTheDocument();
  scrollNearBottom(); expect(api).toHaveBeenCalledTimes(2);
  await user.click(screen.getByRole("button", { name: "重试" }));
  await screen.findByText("tool_19");
  expect(api.mock.calls[1]).toEqual(api.mock.calls[2]);
});

it("reloads an expired catalog and suppresses responses after close", async () => {
  const { api } = setup(); const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await screen.findByText("tool_00");
  api.mockRejectedValueOnce(new Error("INVALID_CURSOR"));
  scrollNearBottom(); await screen.findByText("工具目录已过期或配置已变化，请重新加载。");
  await user.click(screen.getByRole("button", { name: "重试" }));
  await waitFor(() => expect(api).toHaveBeenLastCalledWith({ pluginId: "manual", serverId: "playwright", query: "", cursor: null }));
  await user.click(screen.getByRole("button", { name: "关闭 MCP 详情" }));
  let resolve!: (value: McpToolsResponse) => void;
  api.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await user.click(screen.getByRole("button", { name: "关闭 MCP 详情" }));
  await act(async () => resolve({ state: "ready", description: "迟到的结果" }));
  expect(screen.queryByText("迟到的结果")).not.toBeInTheDocument();
});

it("shows unapproved state and keeps edit and technical controls separate", async () => {
  const { api } = setup(vi.fn().mockResolvedValue({ state: "not_authorized" }), { ...server, consented: false, available: false });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "其他设置" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(api).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  expect(await screen.findByText(/此服务尚未启用或授权/)).toBeInTheDocument();
});

it("displays total count format '共 X 条' and static footer text without timestamp", async () => {
  const user = userEvent.setup();
  setup();
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await screen.findByText("tool_00");

  expect(screen.getByText("共 23 条")).toBeInTheDocument();
  expect(screen.queryByText(/已加载.*\/.*条/)).not.toBeInTheDocument();
  expect(screen.getByText("工具信息以最近一次获取结果为准。")).toBeInTheDocument();
  expect(screen.queryByText(/工具信息获取于/)).not.toBeInTheDocument();
});

it("does not allow expanding tools without parameters or with unsupported schema, and removes explanatory notice text", async () => {
  const user = userEvent.setup();
  const customTools: McpToolDetail[] = [
    {
      name: "tool_no_params",
      description: "没有参数的工具",
      inputSchema: { type: "object" },
    },
    {
      name: "tool_unsupported",
      description: "不支持参数的工具",
      inputSchema: { type: "string" },
      available: false,
    },
    {
      name: "tool_with_params",
      description: "正常有参数的工具",
      inputSchema: { type: "object", properties: { key: { type: "string", description: "测试键" } } },
    },
  ];

  setup(vi.fn().mockResolvedValue({
    state: "ready",
    tools: customTools,
    total: 3,
    catalogId,
  }));

  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await screen.findByText("tool_no_params");

  // Verify non-expandable tool has no <details>
  const noParamsRow = screen.getByText("tool_no_params").closest(".mcp-tool-row");
  expect(noParamsRow?.tagName.toLowerCase()).toBe("div");
  expect(noParamsRow?.querySelector("summary")).toBeNull();
  expect(screen.queryByText(/未声明顶层参数/)).not.toBeInTheDocument();
  expect(screen.queryByText(/完整定义见原始 Schema/)).not.toBeInTheDocument();

  // Verify unavailable tool has no <details> and no notice
  const unsupportedRow = screen.getByText("tool_unsupported").closest(".mcp-tool-row");
  expect(unsupportedRow?.tagName.toLowerCase()).toBe("div");
  expect(screen.queryByText(/参数定义不受支持/)).not.toBeInTheDocument();
  expect(screen.queryByText(/此工具当前不可用/)).not.toBeInTheDocument();

  // Verify both non-expandable tools have "查看原始 Schema" button
  expect(screen.getByRole("button", { name: "查看 tool_no_params 原始 Schema" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "查看 tool_unsupported 原始 Schema" })).toBeInTheDocument();

  // Verify tool with params is expandable
  const withParamsRow = screen.getByText("tool_with_params").closest(".mcp-tool-row");
  expect(withParamsRow?.tagName.toLowerCase()).toBe("details");
  expect(withParamsRow?.querySelector("summary")).not.toBeNull();
});

it("opens and closes the raw schema viewer dialog from 查看原始 Schema", async () => {
  const user = userEvent.setup();
  setup();
  await user.click(screen.getByRole("button", { name: "查看 playwright 工具" }));
  await screen.findByText("tool_00");

  const schemaBtn = screen.getByRole("button", { name: "查看 tool_00 原始 Schema" });
  await user.click(schemaBtn);

  // Schema dialog opens with server name and tool name, without '原始 Schema' text
  const schemaDialog = await screen.findByRole("dialog", { name: /playwright.*tool_00/ });
  expect(schemaDialog).toBeInTheDocument();
  expect(schemaDialog).not.toHaveTextContent("原始 Schema");
  expect(schemaDialog).toHaveTextContent("工具说明 0");
  expect(schemaDialog).toHaveTextContent('"url"');

  // Verify close button uses unified SVG icon
  const closeBtn = screen.getByRole("button", { name: "关闭 tool_00 详情" });
  expect(closeBtn.querySelector("svg")).not.toBeNull();

  // Closing schema dialog via Escape returns to detail dialog
  await user.keyboard("{Escape}");
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: /playwright.*tool_00/ })).not.toBeInTheDocument();
  });
  // MCP detail dialog remains open
  expect(screen.getByRole("dialog", { name: "playwright" })).toBeInTheDocument();
});

it("removes accent focus glow, box-shadow and outlines from search inputs", () => {
  const detailStyles = fs.readFileSync(path.resolve(__dirname, "McpDetailDialog.css"), "utf8");
  expect(detailStyles).toMatch(
    /\.mcp-detail-search input:focus,\s*\.mcp-detail-search input:focus-visible\s*\{[^}]*outline:\s*none;[^}]*box-shadow:\s*none;[^}]*border-color:\s*var\(--line\);/,
  );

  const mcpSettingsStyles = fs.readFileSync(path.resolve(__dirname, "McpSettings.css"), "utf8");
  expect(mcpSettingsStyles).toMatch(
    /\.mcp-search-input:focus,\s*\.mcp-search-input:focus-visible\s*\{[^}]*border-color:\s*var\(--line\);[^}]*outline:\s*none;[^}]*box-shadow:\s*none;/,
  );

  const controlsStyles = fs.readFileSync(path.resolve(__dirname, "../../styles/controls.css"), "utf8");
  expect(controlsStyles).toMatch(
    /input\[type="search"\]:focus,\s*input\[type="search"\]:focus-visible\s*\{[^}]*outline:\s*none;[^}]*box-shadow:\s*none;[^}]*border-color:\s*var\(--line\);/,
  );
});
