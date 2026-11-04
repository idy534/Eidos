import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Item, Run, ToolCall } from "../contracts.js";
import { ExecutionFeed } from "./ExecutionFeed.js";
import { McpPluginNamesContext } from "./McpToolDetails.js";
import { expandDetails } from "../../test/expand-details.js";

const run: Run = {
  id: "run-mcp", sessionId: "session-mcp", status: "running", modelId: "model", modelStepCount: 1,
  createdAt: 1_000, startedAt: 1_000, updatedAt: 1_000,
};

function mcpItem(toolCall: Partial<ToolCall>, status: Item["status"] = "completed"): Item {
  return {
    id: "item-mcp", sessionId: run.sessionId, runId: run.id, ordinal: 1, kind: "tool_call", status, createdAt: 1_000,
    toolCall: {
      id: "tool-mcp", itemId: "item-mcp", modelStepIndex: 1, batchOrder: 0, providerCallId: "provider-mcp",
      toolName: "mcp__github__create_issue", status: status === "in_progress" ? "running" : "completed",
      argumentsJson: JSON.stringify({ repo: "idy534/Eidos", title: "Fix login" }),
      provenance: { kind: "mcp", sourceId: "github:github", sourceVersion: "1", contentHash: "a".repeat(64), pluginId: "github", serverId: "github" },
      startedAt: 1_000, completedAt: 1_400,
      ...toolCall,
    },
  };
}

function browserCall(tool: string, args: object, data: object, extra: Record<string, unknown> = {}): Partial<ToolCall> {
  return {
    toolName: `mcp__browser__${tool}`,
    argumentsJson: JSON.stringify(args),
    provenance: { kind: "mcp", sourceId: "eidos-browser:browser", sourceVersion: "1", contentHash: "b".repeat(64), pluginId: "eidos-browser", serverId: "browser" },
    resultJson: JSON.stringify({ outcome: "success", code: "ok", summary: "MCP tool completed", data: { isError: false, ...data }, ...extra }),
  };
}

function renderItem(item: Item, names: ReadonlyMap<string, string> = new Map([["github", "GitHub"]])) {
  return render(
    <McpPluginNamesContext.Provider value={names}>
      <ExecutionFeed items={[item]} runs={[run]} approvals={[]} />
    </McpPluginNamesContext.Provider>,
  );
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("third-party MCP rows", () => {
  it("names the plugin and tool, shows the result and arguments, and keeps ids in the tooltip", async () => {
    renderItem(mcpItem({
      provenance: { kind: "mcp", sourceId: "github:github", sourceVersion: "1", contentHash: "a".repeat(64), pluginId: "github", serverId: "github", displayTitle: "Create issue" },
      resultJson: JSON.stringify({ outcome: "success", code: "ok", summary: "MCP tool completed", data: { text: "Created #42", isError: false, structuredContent: { number: 42 } } }),
    }));
    expect(screen.getByText("GitHub · Create issue · idy534/Eidos")).toBeInTheDocument();
    await expandDetails();
    expect(screen.getByText("Created #42")).toBeInTheDocument();
    expect(screen.getByText("结构化结果")).toBeInTheDocument();
    expect(screen.getByText("repo")).toBeInTheDocument();
    expect(screen.getByText("Fix login")).toBeInTheDocument();
    const footer = screen.getByText("GitHub（插件）");
    expect(footer).toHaveAttribute("title", "插件 github · 服务器 github · 工具 create_issue");
    expect(screen.queryByText(/MCP tool completed|Plugin github|ms$/)).not.toBeInTheDocument();
  });

  it("shows the server's own error text and long durations in seconds", async () => {
    renderItem(mcpItem({
      completedAt: 4_300,
      resultJson: JSON.stringify({ outcome: "error", code: "mcp_tool_error", summary: "MCP tool completed", data: { text: 'Validation failed: label "p0" does not exist', isError: true } }),
    }, "failed"));
    await expandDetails();
    expect(screen.getByText("MCP 服务器报告了错误")).toBeInTheDocument();
    expect(screen.getByText('Validation failed: label "p0" does not exist')).toBeInTheDocument();
    expect(screen.getByText("GitHub（插件） · 耗时 3.3 秒")).toBeInTheDocument();
    expect(screen.queryByText("mcp_tool_error")).not.toBeInTheDocument();
  });

  it("explains Eidos error codes and uncertain outcomes", async () => {
    renderItem(mcpItem({
      resultJson: JSON.stringify({ outcome: "interrupted", code: "mcp_connection_lost", summary: "", data: {}, reconciliationRequired: true, sideEffectsMayExist: true }),
    }, "failed"));
    await expandDetails();
    expect(screen.getByText("MCP 服务器连接中断")).toBeInTheDocument();
    expect(screen.getByText("结果未知：操作可能已执行")).toBeInTheDocument();
  });
});

describe("browser rows", () => {
  it("shows the screenshot through a Runtime preview URL and enlarges it", async () => {
    const prepare = vi.fn(async () => "eidos-preview://token/tool/0.png");
    const release = vi.fn(async () => undefined);
    vi.stubGlobal("eidosRuntime", { ...window.eidosRuntime, prepareToolContentPreview: prepare, releaseInputPreview: release });
    renderItem(mcpItem(browserCall("browser_take_screenshot", {}, {
      text: "### Result\n- Screenshot of viewport (attached to this result)",
      browser: { page: { url: "https://www.bilibili.com/zh-Hans", title: "bilibili" } },
    }, { attachments: [{ index: 0, kind: "image", mime: "image/png", bytes: 709_312 }] })));
    expect(screen.getByText("截图 · www.bilibili.com")).toBeInTheDocument();
    await expandDetails();
    const thumbnail = await screen.findByRole("button", { name: "放大查看截图" });
    expect(prepare).toHaveBeenCalledWith("session-mcp", "tool-mcp", 0);
    expect(thumbnail).toHaveAttribute("title", "截图 · PNG · 693 KB");
    fireEvent.click(thumbnail);
    expect(screen.getByRole("dialog", { name: "截图预览" })).toBeInTheDocument();
    expect(screen.getByText("bilibili")).toBeInTheDocument();
    expect(screen.queryByText(/Plugin eidos-browser/)).not.toBeInTheDocument();
  });

  it("offers a retry when the screenshot cannot be loaded", async () => {
    const prepare = vi.fn().mockRejectedValueOnce(new Error("gone")).mockResolvedValue("eidos-preview://token/tool/0.png");
    vi.stubGlobal("eidosRuntime", { ...window.eidosRuntime, prepareToolContentPreview: prepare, releaseInputPreview: vi.fn(async () => undefined) });
    renderItem(mcpItem(browserCall("browser_take_screenshot", {}, { text: "" }, { attachments: [{ index: 0, kind: "image" }] })));
    await expandDetails();
    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "放大查看截图" })).toBeInTheDocument());
  });

  it("describes clicks with the page card and tucks Playwright code away", async () => {
    renderItem(mcpItem(browserCall("browser_click", { target: "e12", element: "登录" }, {
      text: "### Ran Playwright code\n```js\nawait page.click()\n```",
      browser: { page: { url: "https://www.bilibili.com/zh-Hans", title: "bilibili" }, code: "await page.getByRole('link').click();", console: { errors: 2, warnings: 0 } },
    })));
    expect(screen.getByText("点击 · 登录 · www.bilibili.com")).toBeInTheDocument();
    await expandDetails(document.body, "details.tool-item");
    expect(screen.getByText("点击「登录」")).toBeInTheDocument();
    expect(screen.getByText("https://www.bilibili.com/zh-Hans")).toBeInTheDocument();
    expect(screen.getByText("控制台有 2 个错误")).toBeInTheDocument();
    expect(screen.getByText("浏览器（内置）")).toBeInTheDocument();
    const technical = screen.getByText("技术详情").closest("details")!;
    expect(technical).not.toHaveAttribute("open");
  });

  it("masks text typed into password fields", async () => {
    renderItem(mcpItem(browserCall("browser_type", { target: "e3", element: "密码输入框", text: "hunter2" }, { text: "" })));
    await expandDetails(document.body, "details.tool-item");
    expect(screen.getByText("在「密码输入框」输入「••••」")).toBeInTheDocument();
    expect(screen.queryByText(/hunter2/)).not.toBeInTheDocument();
  });

  it("titles waits by their duration", () => {
    renderItem(mcpItem(browserCall("browser_wait_for", { time: 25 }, { text: "", browser: { page: { url: "https://www.bilibili.com/" } } })));
    expect(screen.getByText("等待页面 · 25 秒 · www.bilibili.com")).toBeInTheDocument();
  });
});
