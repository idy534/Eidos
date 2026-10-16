import React, { useCallback, useEffect, useState } from "react";
import type {
  ChannelDelivery,
  ChannelPublicConnection,
  ChannelResponse,
  ChannelSaveRequest,
  State,
} from "../../../../shared/channels.js";
import type { ModelListResult, Project } from "../../contracts.js";
import { ToastContainer, type ToastItem, type ToastType } from "../ToastContainer.js";
import { Button } from "../Button.js";
import { StatusBadge, type StatusTone } from "./StatusBadge.js";
import { Toggle } from "./Toggle.js";
import { DeliveryDetailModal } from "./DeliveryDetailModal.js";
import {
  DingTalkForm,
  channelStateLabels as stateLabels,
  channelStateTones as stateTones,
} from "./DingTalkForm.js";
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
  initialChannel?: "list" | "dingtalk";
}

export function ChannelSettings({ modelList, initialChannel = "list" }: ChannelSettingsProps) {
  const [currentView, setCurrentView] = useState<"list" | "dingtalk">(initialChannel);
  const [response, setResponse] = useState<ChannelResponse>();
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
      const value = await window.eidosRuntime.readChannel();
      setResponse(value);
      setError("");
      if (showNotice) {
        showToast("渠道状态已刷新", "success");
      }
    } catch {
      setError("无法读取渠道状态，请检查 Runtime 后刷新。 ");
      showToast("无法读取渠道状态，请检查 Runtime 后刷新。", "error");
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const value = await window.eidosRuntime.readChannel();
        if (active) setResponse(value);
      } catch {
        if (active) setError("无法读取渠道状态，请检查 Runtime 后刷新。 ");
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
  }, []);

  async function save(request: ChannelSaveRequest) {
    setBusy(true);
    setError("");
    try {
      const value = await window.eidosRuntime.saveChannel(request);
      setResponse(value);
      showToast(request.config.enabled ? "配置已保存，Eidos 正在连接钉钉。" : "渠道已停用。", "success");
      return true;
    } catch {
      const errMsg = "渠道保存失败。请检查配置、模型和项目；配置有冲突时请刷新后重试。 ";
      setError(errMsg);
      showToast(errMsg, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const handleToggleDingTalk = async (checked: boolean) => {
    if (!response?.connection) return;
    const { connection } = response;
    await save({
      expectedRevision: connection.revision,
      config: {
        ...connection.config,
        enabled: checked,
      },
    });
  };

  const handleUnsupported = (channelName: string) => {
    showToast(`${channelName}渠道接入功能正在研发中，敬请期待。`, "info");
  };

  const isDingTalkConfigured = Boolean(response?.connection?.config?.clientId);
  const dingTalkState = response?.state ?? "disabled";

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

      {/* 视图 1：接入渠道列表页（对应草图图 2） */}
      {currentView === "list" && (
        <div className="channel-list-view">
          <div className="channel-list-header">
            <div className="channel-header-title-group">
              <h1 className="channel-main-title">接入渠道</h1>
              <p className="channel-main-desc">
                连接外部协作工具，让 Eidos 在不同渠道中接收群聊指令并协同回复
              </p>
            </div>
            <div className="channel-header-actions">
              <Button
                variant="secondary"
                size="small"
                disabled={refreshing || busy}
                onClick={() => void refreshStatus(true)}
                icon={<RefreshIcon spinning={refreshing} />}
                aria-label="刷新渠道状态"
              >
                刷新状态
              </Button>
            </div>
          </div>

          <div className="channel-cards-list">
            {/* 钉钉 */}
            <div
              className={`channel-card channel-card--interactive ${
                isDingTalkConfigured ? "channel-card--configured" : ""
              }`}
            >
              <div
                className="channel-card-left"
                role="button"
                tabIndex={0}
                onClick={() => setCurrentView("dingtalk")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setCurrentView("dingtalk");
                  }
                }}
              >
                <div className="channel-card-logo">
                  <DingTalkLogo />
                </div>
                <div className="channel-card-meta">
                  <div className="channel-card-title-row">
                    <span className="channel-card-name">钉钉</span>
                    <StatusBadge
                      tone={isDingTalkConfigured ? stateTones[dingTalkState] : "neutral"}
                      dot={isDingTalkConfigured}
                    >
                      {isDingTalkConfigured ? stateLabels[dingTalkState] : "未接入"}
                    </StatusBadge>
                  </div>
                  <span className="channel-card-slug">dingtalk</span>
                </div>
              </div>
              <div className="channel-card-right">
                {isDingTalkConfigured ? (
                  <div className="channel-card-actions-group">
                    <Button
                      variant="secondary"
                      size="small"
                      onClick={() => setCurrentView("dingtalk")}
                    >
                      设置
                    </Button>
                    <Toggle
                      id="channel-list-dingtalk-toggle"
                      label="启用或停用钉钉渠道"
                      checked={Boolean(response?.connection?.config?.enabled)}
                      disabled={busy}
                      onChange={(checked) => void handleToggleDingTalk(checked)}
                    />
                  </div>
                ) : (
                  <Button
                    variant="secondary"
                    size="small"
                    onClick={() => setCurrentView("dingtalk")}
                  >
                    接入
                  </Button>
                )}
              </div>
            </div>

            {/* 飞书 */}
            <div className="channel-card channel-card--disabled">
              <div className="channel-card-left">
                <div className="channel-card-logo">
                  <FeishuLogo />
                </div>
                <div className="channel-card-meta">
                  <div className="channel-card-title-row">
                    <span className="channel-card-name">飞书</span>
                    <StatusBadge tone="neutral" dot={false}>
                      未接入
                    </StatusBadge>
                  </div>
                  <span className="channel-card-slug">feishu</span>
                </div>
              </div>
              <div className="channel-card-right">
                <Button
                  variant="secondary"
                  size="small"
                  disabled
                  title="即将支持"
                >
                  接入
                </Button>
              </div>
            </div>

            {/* 企业微信 */}
            <div className="channel-card channel-card--disabled">
              <div className="channel-card-left">
                <div className="channel-card-logo">
                  <WeChatWorkLogo />
                </div>
                <div className="channel-card-meta">
                  <div className="channel-card-title-row">
                    <span className="channel-card-name">企业微信</span>
                    <StatusBadge tone="neutral" dot={false}>
                      未接入
                    </StatusBadge>
                  </div>
                  <span className="channel-card-slug">wechat</span>
                </div>
              </div>
              <div className="channel-card-right">
                <Button
                  variant="secondary"
                  size="small"
                  disabled
                  title="即将支持"
                >
                  接入
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 视图 2：钉钉渠道配置详情页（单行顶栏重构） */}
      {currentView === "dingtalk" && (
        <div className="channel-detail-view">
          {!response && !error ? (
            <>
              <div className="channel-detail-topbar">
                <div className="channel-topbar-left">
                  <button
                    type="button"
                    className="channel-back-link-btn"
                    onClick={() => setCurrentView("list")}
                    aria-label="接入渠道"
                  >
                    <BackArrowIcon />
                    <span>接入渠道</span>
                  </button>
                </div>
              </div>
              <div className="channel-loading-card" role="status">
                <RefreshIcon spinning className="channel-spinner-large" />
                <span>正在读取渠道配置…</span>
              </div>
            </>
          ) : (
            <>
              {/* 表单主体（包含单行顶栏与凭证、运行策略卡片） */}
              <DingTalkForm
                key={response?.connection?.revision ?? 0}
                connection={response?.connection ?? null}
                models={modelList?.models ?? []}
                projects={projects}
                busy={busy}
                onSave={save}
                onBack={() => setCurrentView("list")}
                state={dingTalkState}
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
                    <li>
                      <strong>创建企业应用</strong>：登录钉钉开放平台，在应用开发中创建“企业内部应用”。
                    </li>
                    <li>
                      <strong>开启机器人 Stream 模式</strong>：在“应用功能 - 机器人”中开启机器人，并在消息接收模式选择 <strong>Stream 模式</strong>（无需公网回调域名）。
                    </li>
                    <li>
                      <strong>填入凭证并入群</strong>：填写 Client ID (AppKey) 和 Client Secret (AppSecret)，保存后将机器人加入所属企业群。
                    </li>
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
                  <p className="channel-card-subheading">钉钉渠道近期的消息处理与发送记录</p>
                </div>
                <div className="channel-form-card-body">
                  {!response?.deliveries?.length ? (
                    <div className="channel-empty-deliveries">
                      <p>渠道还没有回复记录。</p>
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
