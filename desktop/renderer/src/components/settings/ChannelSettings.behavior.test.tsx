import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { EidosRuntimeAPI, ModelOption } from "../../contracts.js";
import type { ChannelPublicConnection } from "../../../../shared/channels.js";
import { ChannelSettings } from "./ChannelSettings.js";
import { DingTalkForm } from "./DingTalkForm.js";

const model: ModelOption = {
  id: "deepseek-v4-flash",
  name: "DeepSeek",
  vendor: "DeepSeek",
  provider: "deepseek",
  url: "https://example.com",
  supportsToolCall: true,
  supportsImages: false,
  supportsReasoning: false,
};

const connection: ChannelPublicConnection = {
  id: "connection",
  revision: 2,
  hasSecret: true,
  config: {
    enabled: true,
    clientId: "app",
    modelId: model.id,
    approvalMode: "manual",
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("loads connection state, navigates to dingtalk details, masks credentials and shows unknown delivery reasons", async () => {
  const user = userEvent.setup();
  window.eidosRuntime = {
    readChannel: vi.fn().mockResolvedValue({
      connection,
      state: "connected",
      deliveries: [
        { id: "reply", status: "unknown", text: "任务已完成。", reason: "网络中断，结果未知。" },
      ],
    }),
    listProjects: vi.fn().mockResolvedValue({ items: [] }),
  } as unknown as EidosRuntimeAPI;

  render(<ChannelSettings modelList={{ models: [model] }} />);
  expect(await screen.findByText("已连接")).toBeInTheDocument();

  // 点击“设置”进入钉钉配置详情页
  await user.click(screen.getByRole("button", { name: "设置" }));

  expect(screen.getByLabelText("Client Secret")).toHaveAttribute("type", "password");
  expect(screen.getByLabelText("Client Secret")).toHaveValue("");
  expect(screen.getByText("发送结果未知")).toBeInTheDocument();
  expect(screen.getByText("网络中断，结果未知。")).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "完全访问" })).not.toBeInTheDocument();

  // 点击返回按钮回到接入渠道列表页
  await user.click(screen.getByRole("button", { name: "接入渠道" }));
  expect(screen.getByRole("heading", { name: "接入渠道" })).toBeInTheDocument();
});

it("renders channel list with DingTalk at the top and disabled buttons for Feishu and WeChat", async () => {
  const user = userEvent.setup();
  window.eidosRuntime = {
    readChannel: vi.fn().mockResolvedValue({ state: "disabled" }),
    listProjects: vi.fn().mockResolvedValue({ items: [] }),
  } as unknown as EidosRuntimeAPI;

  render(<ChannelSettings modelList={{ models: [model] }} />);
  expect(await screen.findByRole("heading", { name: "接入渠道" })).toBeInTheDocument();
  expect(screen.getByText("钉钉")).toBeInTheDocument();
  expect(screen.getByText("飞书")).toBeInTheDocument();
  expect(screen.getByText("企业微信")).toBeInTheDocument();

  // 钉钉排在最前，接入按钮可点击；飞书和企业微信接入按钮置灰不可点击
  const accessButtons = screen.getAllByRole("button", { name: "接入" });
  expect(accessButtons).toHaveLength(3);
  const dingTalkButton = accessButtons[0]!;
  const feishuButton = accessButtons[1]!;
  const wechatButton = accessButtons[2]!;

  expect(dingTalkButton).not.toBeDisabled();
  expect(feishuButton).toBeDisabled();
  expect(wechatButton).toBeDisabled();

  // 钉钉未配置时点击接入进入钉钉详情页
  await user.click(dingTalkButton);
  expect(await screen.findByRole("heading", { name: "应用凭证" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "保存渠道配置" })).toBeInTheDocument();
});

it("shows floating toast on refresh and supports manual close", async () => {
  const user = userEvent.setup();
  window.eidosRuntime = {
    readChannel: vi.fn().mockResolvedValue({ state: "disabled" }),
    listProjects: vi.fn().mockResolvedValue({ items: [] }),
  } as unknown as EidosRuntimeAPI;

  render(<ChannelSettings modelList={{ models: [model] }} />);
  expect(await screen.findByRole("heading", { name: "接入渠道" })).toBeInTheDocument();

  // 点击刷新状态
  await user.click(screen.getByRole("button", { name: "刷新渠道状态" }));
  expect(await screen.findByText("渠道状态已刷新")).toBeInTheDocument();

  // 手动关闭
  const closeBtn = screen.getByRole("button", { name: "关闭提示" });
  await user.click(closeBtn);
  await waitFor(() => {
    expect(screen.queryByText("渠道状态已刷新")).not.toBeInTheDocument();
  });
});

it("automatically dismisses toast notification after 3.5 seconds", async () => {
  vi.useFakeTimers();
  window.eidosRuntime = {
    readChannel: vi.fn().mockResolvedValue({ state: "disabled" }),
    listProjects: vi.fn().mockResolvedValue({ items: [] }),
  } as unknown as EidosRuntimeAPI;

  render(<ChannelSettings modelList={{ models: [model] }} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  const refreshBtn = screen.getByRole("button", { name: "刷新渠道状态" });
  await act(async () => {
    fireEvent.click(refreshBtn);
    await vi.advanceTimersByTimeAsync(0);
  });

  expect(screen.getByText("渠道状态已刷新")).toBeInTheDocument();

  // 推进 3500 毫秒后自动关闭
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3500);
  });

  expect(screen.queryByText("渠道状态已刷新")).not.toBeInTheDocument();
  vi.useRealTimers();
});

it("saves only current options and retains an existing secret without resending it", async () => {
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue(true);
  render(<DingTalkForm connection={connection} models={[model]} projects={[]} busy={false} onSave={save} />);
  expect(screen.queryByText("允许单聊")).not.toBeInTheDocument();
  expect(screen.queryByText("访问白名单")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("允许的用户 UserID")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("允许的群 ConversationID")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("机器人 RobotCode")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("组织 CorpID")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "保存渠道配置" }));
  expect(save).toHaveBeenCalledOnce();
  const request = save.mock.calls[0]![0];
  expect(request.expectedRevision).toBe(2);
  expect(request.config).toEqual(connection.config);
  expect(request).not.toHaveProperty("clientSecret");
});

it("creates a connection with just app credentials and the default model and permissions", async () => {
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue(true);
  render(<DingTalkForm connection={null} models={[model]} projects={[]} busy={false} onSave={save} />);
  await user.type(screen.getByLabelText("Client ID"), "new-app");
  await user.type(screen.getByLabelText("Client Secret"), "new-secret");
  await user.click(screen.getByLabelText("启用钉钉渠道"));
  await user.click(screen.getByRole("button", { name: "保存渠道配置" }));
  await waitFor(() => expect(save).toHaveBeenCalledOnce());
  expect(save).toHaveBeenCalledWith({
    expectedRevision: 0,
    clientSecret: "new-secret",
    config: { enabled: true, clientId: "new-app", modelId: model.id, projectId: null, approvalMode: "manual" },
  });
  expect(screen.getByLabelText("Client Secret")).toHaveValue("");
});

it("requires a fresh secret when changing the app and preserves it after failure", async () => {
  const user = userEvent.setup();
  const save = vi.fn().mockResolvedValue(false);
  render(<DingTalkForm connection={connection} models={[model]} projects={[]} busy={false} onSave={save} />);
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
  window.eidosRuntime = {
    readChannel: vi.fn().mockRejectedValue(new Error("unavailable")),
    listProjects: vi.fn().mockResolvedValue({ items: [] }),
  } as unknown as EidosRuntimeAPI;
  render(<ChannelSettings modelList={{ models: [model] }} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("无法读取渠道状态");
  expect(screen.queryByRole("button", { name: "保存渠道配置" })).not.toBeInTheDocument();
});

it("renders unified single topbar in detail view with back button, identity, toggle, and square refresh button", async () => {
  const user = userEvent.setup();
  const readChannel = vi.fn().mockResolvedValue({
    connection,
    state: "connected",
    deliveries: [],
  });
  window.eidosRuntime = {
    readChannel,
    listProjects: vi.fn().mockResolvedValue({ items: [] }),
    saveChannel: vi.fn().mockResolvedValue({ connection, state: "connected" }),
  } as unknown as EidosRuntimeAPI;

  render(<ChannelSettings modelList={{ models: [model] }} initialChannel="dingtalk" />);

  // 顶栏元素验证
  expect(await screen.findByRole("heading", { name: "钉钉" })).toBeInTheDocument();
  expect(screen.getByText("已连接")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "接入渠道" })).toBeInTheDocument();
  expect(screen.getByLabelText("启用钉钉渠道")).toBeChecked();

  // 顶栏小方块刷新按钮
  const refreshBtn = screen.getByRole("button", { name: "刷新状态" });
  expect(refreshBtn).toBeInTheDocument();
  await user.click(refreshBtn);
  expect(readChannel).toHaveBeenCalled();
  expect(await screen.findByText("渠道状态已刷新")).toBeInTheDocument();

  // 旧的冗余描述卡片已被移除
  expect(screen.queryByText(/Eidos 运行时可以接收并回复钉钉消息/)).not.toBeInTheDocument();
  expect(screen.queryByText(/开启后，Eidos 将在后台连接钉钉 Stream/)).not.toBeInTheDocument();

  // 顶栏下方直接呈现“应用凭证”
  expect(screen.getByRole("heading", { name: "应用凭证" })).toBeInTheDocument();
});

it("clamps long deliveries to 2 lines and opens modal with rich markdown on clicking anywhere on the item", async () => {
  const user = userEvent.setup();
  const longDeliveryText = `工作区目前的内容：\n\n**成品文档 (8 个)**\n- 第一项\n- 第二项\n\n\`build_maldives_pdf.py\``;

  window.eidosRuntime = {
    readChannel: vi.fn().mockResolvedValue({
      connection,
      state: "connected",
      deliveries: [
        {
          id: "delivery-1",
          status: "sent",
          text: longDeliveryText,
          createdAt: "2026-10-16T15:05:00Z",
        },
      ],
    }),
    listProjects: vi.fn().mockResolvedValue({ items: [] }),
  } as unknown as EidosRuntimeAPI;

  render(<ChannelSettings modelList={{ models: [model] }} initialChannel="dingtalk" />);
  expect(await screen.findByText("已发送")).toBeInTheDocument();

  // 默认两行折叠态：两行截断样式
  const collapsedText = screen.getByText(/工作区目前的内容/);
  expect(collapsedText).toHaveClass("channel-delivery-text--collapsed");

  // 不存在“查看全文 ▾”、“收起 ▴”或“弹窗查看 ↗”等多余内联按钮
  expect(screen.queryByRole("button", { name: /查看全文/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /收起/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /弹窗查看/ })).not.toBeInTheDocument();

  // 点击整个会话卡片任意处：直接弹出全文对话框
  const deliveryCard = screen.getByRole("button", { name: /查看回复详情/ });
  await user.click(deliveryCard);

  // 弹窗展开，包含正文风格的 MarkdownContent 与复制按钮
  const dialog = screen.getByRole("dialog");
  expect(dialog).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "回复详情" })).toBeInTheDocument();
  expect(screen.getByText("成品文档 (8 个)")).toBeInTheDocument();
  expect(screen.getByText("第一项")).toBeInTheDocument();
  expect(screen.getByText("build_maldives_pdf.py")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "复制内容" })).toBeInTheDocument();

  // 关闭弹窗
  await user.click(screen.getByRole("button", { name: "关闭回复详情" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});


