import React, { useCallback, useEffect, useState } from "react";
import type {
  Channel,
  ChannelDelivery,
  ChannelResponse,
  ChannelSaveRequest,
} from "../../../../shared/channels.js";
import type { ModelListResult, Project } from "../../contracts.js";
import { ToastContainer, type ToastItem, type ToastType } from "../ToastContainer.js";
import { Button } from "../Button.js";
import { StatusBadge, type StatusTone } from "./StatusBadge.js";
import { Toggle } from "./Toggle.js";
import { DeliveryDetailModal } from "./DeliveryDetailModal.js";
import {
  ChannelForm,
  channelStateLabels as stateLabels,
  channelStateTones as stateTones,
} from "./ChannelForm.js";
import {
  DingTalkLogo,
  FeishuLogo,
  WeChatWorkLogo,
  RefreshIcon,
  BackArrowIcon,
  InfoCircleIcon,
} from "./ChannelIcons.js";

const deliveryStateLabels: Record<string, string> = {
  pending: "等待发送",
  sending: "正在发送",
  sent: "已发送",
  failed: "发送失败",
  unknown: "发送结果未知",
};

const deliveryStateTones: Record<string, StatusTone> = {
  pending: "neutral",
  sending: "info",
  sent: "success",
  failed: "danger",
  unknown: "warning",
};

const previewText = (text: string) => text.replace(/\r\n/g, "\n").replace(/\n{2,}/g, "\n").trim();

interface ChannelSettingsProps {
  modelList?: ModelListResult | undefined;
  initialChannel?: "list" | Channel;
}

export function ChannelSettings({ modelList, initialChannel = "list" }: ChannelSettingsProps) {
  const [currentView, setCurrentView] = useState<"list" | Channel>(initialChannel);
  const [responses, setResponses] = useState<Partial<Record<Channel, ChannelResponse>>>({});
  const selected = currentView === "list" ? "dingtalk" : currentView;
  const response = responses[selected];
  const selectedName = selected === "feishu" ? "飞书" : selected === "wecom" ? "企业微信" : "钉钉";
  const load = useCallback(async () => {
    const entries = await Promise.all((["dingtalk", "feishu", "wecom"] as const).map(async (channel) => [channel, await window.eidosRuntime.readChannel({ channel })] as const));
    return Object.fromEntries(entries) as Record<Channel, ChannelResponse>;
  }, []);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [activeModalDelivery, setActiveModalDelivery] = useState<ChannelDelivery | null>(null);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback((message: string, type: ToastType = "info") => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setToasts((prev) => [...prev, { id, message, type, duration: 3500 }]);
    setTimeout(() => {
      dismissToast(id);
    }, 3500);
  }, [dismissToast]);

  const refreshStatus = async (showNotice = false) => {
    setRefreshing(true);
    try {
      const value = await load();
      setResponses(value);
      setError("");
      if (showNotice) {
        showToast("助理状态已刷新", "success");
      }
    } catch {
      setError("无法读取助理状态，请检查 Runtime 后刷新。 ");
      showToast("无法读取助理状态，请检查 Runtime 后刷新。", "error");
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const value = await load();
        if (active) setResponses(value);
      } catch {
        if (active) setError("无法读取助理状态，请检查 Runtime 后刷新。 ");
      }
      if (active) {
        timer = setTimeout(() => {
          void poll();
        }, 3000);
      }
    }

    void window.eidosRuntime
      .listProjects()
      .then((value) => {
        if (active) setProjects(value.items);
      })
      .catch(() => {
        if (active) setError("无法读取项目列表，请重新打开设置。 ");
      });

    void poll();

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [load]);

  async function save(request: ChannelSaveRequest) {
    setBusy(true);
    setError("");
    try {
      const value = await window.eidosRuntime.saveChannel(request);
      setResponses((old) => ({ ...old, [request.channel ?? "dingtalk"]: value }));
      showToast(request.config.enabled ? "配置已保存，Eidos 正在连接。" : "助理已停用。", "success");
      return true;
    } catch {
      const errMsg = "助理保存失败。请检查配置、模型和项目；配置有冲突时请刷新后重试。 ";
      setError(errMsg);
      showToast(errMsg, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const handleToggle = async (channel: Channel, checked: boolean) => {
    const connection = responses[channel]?.connection;
    if (!connection) return;
    await save({
      channel,
      expectedRevision: connection.revision,
      config: {
        ...connection.config,
        enabled: checked,
      },
    });
  };

  return (
    <div className="settings-panel channel-settings-page">
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* 仅在初始化读取失败且无数据时展示静态错误横幅 */}
      {!response && error && (
        <div className="channel-alert-banner channel-alert-banner--error" role="alert">
          <span className="channel-alert-icon">!</span>
          <span className="channel-alert-message">{error}</span>
        </div>
      )}

      {/* 视图 1：接入助理列表页（对应草图图 2） */}
      {currentView === "list" && (
        <div className="channel-list-view">
          <div className="channel-list-header">
            <div className="channel-header-title-group">
              <h1 className="channel-main-title">接入助理</h1>
              <p className="channel-main-desc">
                连接外部协作工具，让 Eidos 在不同平台中作为助理接收群聊指令并协同回复
              </p>
            </div>
            <div className="channel-header-actions">
              <Button
                variant="secondary"
                size="small"
                disabled={refreshing || busy}
                onClick={() => void refreshStatus(true)}
                icon={<RefreshIcon spinning={refreshing} />}
                aria-label="刷新助理状态"
              >
                刷新状态
              </Button>
            </div>
          </div>

          <div className="channel-cards-list">
            {(["dingtalk", "feishu", "wecom"] as const).map((channel) => {
              const value = responses[channel];
              const configured = Boolean(value?.connection);
              const name = channel === "dingtalk" ? "钉钉" : channel === "feishu" ? "飞书" : "企业微信";
              const status = value?.state ?? "disabled";
              return <div key={channel} className={`channel-card channel-card--interactive ${configured ? "channel-card--configured" : ""}`}>
                <div className="channel-card-left" role="button" tabIndex={0} aria-label={`${name}设置`}
                  onClick={() => setCurrentView(channel)} onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setCurrentView(channel); }
                  }}>
                  <div className="channel-card-logo">{channel === "dingtalk" ? <DingTalkLogo /> : channel === "feishu" ? <FeishuLogo /> : <WeChatWorkLogo />}</div>
                  <div className="channel-card-meta">
                    <div className="channel-card-title-row"><span className="channel-card-name">{name}</span>
                      <StatusBadge tone={configured ? stateTones[status] : "neutral"} dot={configured}>
                        {configured ? stateLabels[status] : "未接入"}
                      </StatusBadge>
                    </div><span className="channel-card-slug">{channel}</span>
                  </div>
                </div>
                <div className="channel-card-right"><div className="channel-card-actions-group">
                  <Button variant="secondary" size="small" disabled={busy} onClick={() => setCurrentView(channel)}>{configured ? "设置" : "接入"}</Button>
                  {configured && <Toggle id={`channel-list-${channel}-toggle`} label={`启用或停用${name}助理`}
                    checked={Boolean(value?.connection?.config.enabled)} disabled={busy} onChange={(checked) => void handleToggle(channel, checked)} />}
                </div></div>
              </div>;
            })}

          </div>
        </div>
      )}

      {/* 视图 2：钉钉助理配置详情页（单行顶栏重构） */}
      {currentView !== "list" && (
        <div className="channel-detail-view">
          {!response && !error ? (
            <>
              <div className="channel-detail-topbar">
                <div className="channel-topbar-left">
                  <button
                    type="button"
                    className="channel-back-link-btn"
                    onClick={() => setCurrentView("list")}
                    aria-label="接入助理"
                  >
                    <BackArrowIcon />
                    <span>接入助理</span>
                  </button>
                </div>
              </div>
              <div className="channel-loading-card" role="status">
                <RefreshIcon spinning className="channel-spinner-large" />
                <span>正在读取助理配置…</span>
              </div>
            </>
          ) : (
            <>
              {/* 表单主体（包含单行顶栏与凭证、运行策略卡片） */}
              <ChannelForm
                key={`${selected}:${response?.connection?.revision ?? 0}`}
                channel={selected}
                connection={response?.connection ?? null}
                models={modelList?.models ?? []}
                projects={projects}
                busy={busy}
                onSave={save}
                onBack={() => setCurrentView("list")}
                state={response?.state ?? "disabled"}
                reason={response?.reason}
                refreshing={refreshing}
                onRefresh={() => void refreshStatus(true)}
              />

              {/* 钉钉接入配置指引 */}
              <div className="channel-form-card channel-guide-card">
                <div className="channel-form-card-header">
                  <div className="channel-guide-header-title">
                    <InfoCircleIcon />
                    <h3 className="channel-card-heading">接入配置指引</h3>
                  </div>
                </div>
                <div className="channel-form-card-body">
                  <ol className="channel-guide-steps">
                    {selected === "feishu" ? <>
                      <li>在飞书开放平台创建自建应用，并开启机器人能力。</li>
                      <li>添加单聊消息读取权限 im:message.p2p_msg:readonly、群聊 @ 消息权限 im:message.group_at_msg:readonly 和发送权限 im:message:send_as_bot。</li>
                      <li>填写 App ID 和 App Secret，启用并保存；在飞书后台选择“使用长连接接收事件”，订阅 im.message.receive_v1。</li>
                      <li>发布应用版本并设置可用范围，将机器人加入群聊。</li>
                    </> : selected === "wecom" ? <>
                      <li>在企业微信创建智能机器人，选择 API 模式，并使用长连接接收消息。</li>
                      <li>填写机器人提供的 Bot ID 和 Secret，启用并保存。此处不使用普通群 Webhook。</li>
                      <li>设置机器人的可用范围，将机器人加入群聊。单聊直接发消息，群聊需要 @ 机器人。</li>
                    </> : <>
                      <li><strong>创建企业应用</strong>：登录钉钉开放平台，创建“企业内部应用”。</li>
                      <li><strong>开启机器人 Stream 模式</strong>：开启机器人，并选择 Stream 模式（无需公网回调域名）。</li>
                      <li><strong>填入凭证并入群</strong>：填写 Client ID 和 Client Secret，发布应用后将机器人加入内部群。</li>
                    </>}
                  </ol>
                  <div className="channel-guide-notice">
                    <p>
                       机器人默认支持单聊，群消息需要 @ 机器人。电脑休眠或退出 Eidos 时机器人无法接收消息；同一应用凭证请只在一台 Eidos 客户端上启用。
                    </p>
                  </div>
                </div>
              </div>

              {/* 最近回复记录 */}
              <div className="channel-form-card channel-deliveries-card">
                <div className="channel-form-card-header">
                  <h3 className="channel-card-heading">最近回复</h3>
                  <p className="channel-card-subheading">{selectedName}助理近期的消息处理与发送记录</p>
                </div>
                <div className="channel-form-card-body">
                  {!response?.deliveries?.length ? (
                    <div className="channel-empty-deliveries">
                      <p>助理还没有回复记录。</p>
                    </div>
                  ) : (
                    <ul className="channel-deliveries-list">
                      {response.deliveries.map((delivery) => (
                        <li
                          key={delivery.id}
                          className="channel-delivery-item channel-delivery-item--interactive"
                          role="button"
                          tabIndex={0}
                          onClick={() => setActiveModalDelivery(delivery)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              setActiveModalDelivery(delivery);
                            }
                          }}
                          title="点击查看完整回复"
                          aria-label={`查看回复详情，状态：${deliveryStateLabels[delivery.status] ?? delivery.status}`}
                        >
                          <div className="channel-delivery-item-header">
                            <StatusBadge
                              tone={deliveryStateTones[delivery.status] ?? "neutral"}
                              dot
                            >
                              {deliveryStateLabels[delivery.status] ?? delivery.status}
                            </StatusBadge>
                            {delivery.createdAt && (
                              <span className="channel-delivery-time">
                                {new Date(delivery.createdAt).toLocaleTimeString([], {
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </span>
                            )}
                          </div>

                          <p className="channel-delivery-text channel-delivery-text--collapsed">
                            {previewText(delivery.text)}
                          </p>

                          {delivery.reason && (
                            <div className="channel-delivery-reason">
                              <span>{delivery.reason}</span>
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* 回复详情弹窗 */}
      {activeModalDelivery && (
        <DeliveryDetailModal
          delivery={activeModalDelivery}
          stateLabel={deliveryStateLabels[activeModalDelivery.status] ?? activeModalDelivery.status}
          stateTone={deliveryStateTones[activeModalDelivery.status] ?? "neutral"}
          onClose={() => setActiveModalDelivery(null)}
          onCopy={() => {
            void navigator.clipboard.writeText(activeModalDelivery.text);
            showToast("回复内容已复制到剪贴板", "success");
          }}
        />
      )}
    </div>
  );
}
