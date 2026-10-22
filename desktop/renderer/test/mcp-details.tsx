import { createRoot } from "react-dom/client";
import { McpSettings } from "../src/components/settings/McpSettings.js";
import type { McpToolsRequest, McpToolDetail } from "../../shared/mcp-details.js";
import "../src/styles.css";

const tools: McpToolDetail[] = Array.from({ length: 23 }, (_, index) => ({
  name: index === 0 ? "browser_navigate" : `browser_tool_${index}`,
  description: index === 0 ? "打开指定网页。" : `此工具提供第 ${index} 项浏览器操作。`,
  inputSchema: { type: "object", properties: { url: { type: "string", description: "目标网页地址" } }, required: ["url"] },
}));
let calls = 0;
Object.assign(window, { eidosRuntime: { readMcpTools: async (request: McpToolsRequest) => {
  calls++;
  await new Promise(resolve => setTimeout(resolve, 50));
  const list = tools.filter(tool => `${tool.name} ${tool.description}`.includes(request.query || ""));
  const offset = Number(request.cursor?.split(":")[1] || 0);
  return { state: "ready", description: "使用浏览器访问网页、操作页面并获取内容。", tools: list.slice(offset, offset + 10), total: list.length,
    catalogId: "00000000-0000-4000-8000-000000000001", nextCursor: offset + 10 < list.length ? `00000000-0000-4000-8000-000000000001:${offset + 10}` : null, fetchedAt: Date.now() };
} }, getMcpCalls: () => calls });
const noop = async () => {};
createRoot(document.getElementById("root")!).render(<div className="settings-page"><McpSettings servers={[{
  schemaVersion: 1, pluginId: "manual", pluginVersion: "manual", pluginHash: "fixture", serverId: "playwright",
  executable: "npx", argv: ["@playwright/mcp@latest"], envNames: [], permissionProfile: "connector",
  startupTimeoutSeconds: 15, toolTimeoutSeconds: 60, declaredEnabled: true, consented: true, available: true, updatedAt: 1,
}]} pendingAction={null} onToggleMcp={noop} onCreateMcp={noop} onUpdateMcp={noop} onRemoveMcp={noop} onShowToast={() => {}} /></div>);
