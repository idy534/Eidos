import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI, ModelOption } from "../../contracts.js";
import type { ChannelPublicConnection } from "../../../../shared/channels.js";
import { ChannelSettings } from "./ChannelSettings.js";
import { DingTalkForm } from "./DingTalkForm.js";

const model: ModelOption = { id: "deepseek-v4-flash", name: "DeepSeek", vendor: "DeepSeek", provider: "deepseek",
  url: "https://example.com", supportsToolCall: true, supportsImages: false, supportsReasoning: false };
const connection: ChannelPublicConnection = { id: "connection", revision: 2, hasSecret: true, config: {
  enabled: true, clientId: "app", robotCode: "robot", corpId: "corp", modelId: model.id,
  allowedUserIds: ["owner"], allowedConversationIds: ["group"], approvalMode: "manual",
} };

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("loads connection state, masks credentials and shows unknown delivery reasons", async () => {
  window.eidosRuntime = { readChannel: vi.fn().mockResolvedValue({ connection, state: "connected", deliveries: [
    { id: "reply", status: "unknown", text: "任务已完成。", reason: "网络中断，结果未知。" },
  ] }), listProjects: vi.fn().mockResolvedValue({ items: [] }) } as unknown as EidosRuntimeAPI;
  render(<ChannelSettings modelList={{ models: [model] }}/>);
  expect(await screen.findByText("已连接")).toBeInTheDocument();
  expect(screen.getByLabelText("Client Secret")).toHaveAttribute("type", "password");
  expect(screen.getByLabelText("Client Secret")).toHaveValue("");
  expect(screen.getByText("发送结果未知")).toBeInTheDocument();
  expect(screen.getByText("网络中断，结果未知。")).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "完全访问" })).not.toBeInTheDocument();
});

it("saves explicit allowlists and retains an existing secret without resending it", async () => {
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue(true);
  render(<DingTalkForm connection={connection} models={[model]} projects={[]} busy={false} onSave={save}/>);
  await user.clear(screen.getByLabelText("允许的用户 UserID"));
  await user.type(screen.getByLabelText("允许的用户 UserID"), "owner, second\nowner");
  await user.click(screen.getByRole("button", { name: "保存渠道配置" }));
  expect(save).toHaveBeenCalledOnce();
  const request = save.mock.calls[0]![0];
  expect(request.expectedRevision).toBe(2);
  expect(request.config.allowedUserIds).toEqual(["owner", "second"]);
  expect(request).not.toHaveProperty("clientSecret");
});

it("requires a fresh secret when changing the app and preserves it after failure", async () => {
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue(false);
  render(<DingTalkForm connection={connection} models={[model]} projects={[]} busy={false} onSave={save}/>);
  await user.clear(screen.getByLabelText("Client ID"));
  await user.type(screen.getByLabelText("Client ID"), "other");
  await user.click(screen.getByRole("button", { name: "保存渠道配置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Client Secret");
  expect(save).not.toHaveBeenCalled();
  await user.type(screen.getByLabelText("Client Secret"), "new-secret");
  await user.click(screen.getByRole("button", { name: "保存渠道配置" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(screen.getByLabelText("Client Secret")).toHaveValue("new-secret");
});

it("shows read errors and does not fabricate a disconnected configuration", async () => {
  window.eidosRuntime = { readChannel: vi.fn().mockRejectedValue(new Error("unavailable")),
    listProjects: vi.fn().mockResolvedValue({ items: [] }) } as unknown as EidosRuntimeAPI;
  render(<ChannelSettings modelList={{ models: [model] }}/>);
  expect(await screen.findByRole("alert")).toHaveTextContent("无法读取渠道状态");
  expect(screen.queryByRole("button", { name: "保存渠道配置" })).not.toBeInTheDocument();
});
