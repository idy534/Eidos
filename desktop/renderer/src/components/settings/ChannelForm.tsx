import React, { useState } from "react";
import type { Channel, ChannelConfig, ChannelPublicConnection, ChannelSaveRequest, State } from "../../../../shared/channels.js";
import type { ModelOption, Project } from "../../contracts.js";
import { ModelSelector } from "../ModelSelector.js";
import { Button } from "../Button.js";
import { StatusBadge, type StatusTone } from "./StatusBadge.js";
import { Toggle } from "./Toggle.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { BackArrowIcon, DingTalkLogo, FeishuLogo, WeChatWorkLogo, EyeIcon, EyeOffIcon, RefreshIcon } from "./ChannelIcons.js";

export const channelStateLabels: Record<State, string> = {
  disabled: "未连接",
  connecting: "正在连接",
  connected: "已连接",
  error: "连接异常",
};

export const channelStateTones: Record<State, StatusTone> = {
  disabled: "neutral",
  connecting: "info",
  connected: "success",
  error: "danger",
};

interface Props {
  channel?: Channel;
  connection: ChannelPublicConnection | null;
  models: ModelOption[];
  projects: Project[];
  busy: boolean;
  onSave: (request: ChannelSaveRequest) => Promise<boolean>;
  onBack?: (() => void) | undefined;
  state?: State | undefined;
  reason?: string | null | undefined;
  refreshing?: boolean | undefined;
  onRefresh?: (() => void) | undefined;
}

export function ChannelForm({
  channel = "dingtalk",
  connection,
  models,
  projects,
  busy,
  onSave,
  onBack,
  state,
  reason,
  refreshing = false,
  onRefresh,
}: Props) {
  const name = channel === "feishu" ? "飞书" : channel === "wecom" ? "企业微信" : "钉钉";
  const idLabel = channel === "feishu" ? "App ID" : channel === "wecom" ? "Bot ID" : "Client ID";
  const secretLabel = channel === "feishu" ? "App Secret" : channel === "wecom" ? "Secret" : "Client Secret";
  const [config, setConfig] = useState<ChannelConfig>(connection?.config ?? {
    enabled: false, clientId: "", modelId: models[0]?.id ?? "",
    projectId: null, approvalMode: "manual",
  });
  const [secret, setSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [error, setError] = useState("");
  const [showFullAccessDialog, setShowFullAccessDialog] = useState(false);
  const [fullAccessConfirmed, setFullAccessConfirmed] = useState(false);

  const change = <K extends keyof ChannelConfig>(key: K, value: ChannelConfig[K]) => setConfig((old) => ({ ...old, [key]: value }));
  const missingProject = config.projectId && !projects.some((project) => project.id === config.projectId);

  return (
    <form
      className="channel-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (showFullAccessDialog) return;
        setError("");
        if (!config.modelId) { setError("请选择模型。"); return; }
        if (!secret && (!connection?.hasSecret || config.clientId !== connection.config.clientId)) {
          setError(`请填写 ${secretLabel}。`);
          return;
        }
        void onSave({
          channel,
          expectedRevision: connection?.revision ?? 0,
          config,
          ...(config.approvalMode === "full_access" && fullAccessConfirmed ? { fullAccessConfirmation: "full-access-v1" as const } : {}),
          ...(secret ? { clientSecret: secret } : {}),
        }).then((saved) => {
          if (saved) setSecret("");
        });
      }}
    >
      {/* 顶部单行栏：返回按钮 + 钉钉标识与连接状态 + 启用开关 + 刷新按钮 */}
      <div className="channel-detail-topbar">
        <div className="channel-topbar-left">
          {onBack && (
            <>
              <button
                type="button"
                className="channel-back-link-btn"
                onClick={onBack}
                aria-label="接入助理"
              >
                <BackArrowIcon />
                <span>接入助理</span>
              </button>
              <div className="channel-topbar-divider" />
            </>
          )}
          <div className="channel-topbar-identity">
            <div className="channel-topbar-logo">
              {channel === "feishu" ? <FeishuLogo size={24} /> : channel === "wecom" ? <WeChatWorkLogo size={24} /> : <DingTalkLogo size={24} />}
            </div>
            <h2 className="channel-topbar-title">{name}</h2>
            {state && (
              <StatusBadge tone={channelStateTones[state]} dot>
                {channelStateLabels[state]}
              </StatusBadge>
            )}
          </div>
        </div>

        <div className="channel-topbar-right">
          <div
            className="channel-topbar-toggle-wrapper"
            title={`点击${config.enabled ? "停用" : "启用"}${name}助理`}
          >
            <Toggle
              id="field-channel-enabled"
              label={`启用${name}助理`}
              checked={config.enabled ?? false}
              disabled={busy}
              onChange={(checked) => change("enabled", checked)}
            />
          </div>
          {onRefresh && (
            <button
              type="button"
              className="channel-topbar-refresh-btn"
              disabled={busy || refreshing}
              onClick={onRefresh}
              title="刷新状态"
              aria-label="刷新状态"
            >
              <RefreshIcon spinning={refreshing} size={15} />
            </button>
          )}
        </div>
      </div>

      {reason && (
        <div className="channel-alert-banner channel-alert-banner--info" role="status">
          <span className="channel-alert-icon">ℹ</span>
          <span className="channel-alert-message">{reason}</span>
        </div>
      )}

      <fieldset disabled={busy} className="channel-fieldset">
        <legend className="sr-only">{name}连接配置</legend>

        {/* 应用凭证卡片 */}
        <div className="channel-form-card">
          <div className="channel-form-card-header">
            <h3 className="channel-card-heading">应用凭证</h3>
            <p className="channel-card-subheading">{channel === "feishu" ? "在飞书开放平台创建的自建应用凭证（需启用长连接）" : channel === "wecom" ? "在企业微信创建智能机器人，选择 API 模式和长连接，获取 Bot ID 与 Secret" : "在钉钉开放平台创建的企业内部应用凭证（需启用 Stream 模式）"}</p>
          </div>
          <div className="channel-form-card-body">
            <div className="channel-grid-fields">
              <div className="channel-field">
                <label htmlFor="field-client-id">{idLabel}</label>
                <input
                  id="field-client-id"
                  required
                  maxLength={256}
                  value={config.clientId}
                  autoComplete="off"
                  placeholder={`请输入 ${idLabel}`}
                  onChange={(event) => change("clientId", event.target.value.trim())}
                />
              </div>

              <div className="channel-field">
                <label htmlFor="field-client-secret">{secretLabel}</label>
                <div className="channel-input-with-action">
                  <input
                    id="field-client-secret"
                    type={showSecret ? "text" : "password"}
                    maxLength={1024}
                    value={secret}
                    autoComplete="new-password"
                    placeholder={connection?.hasSecret ? "留空保留已保存的凭证" : `请输入 ${secretLabel}`}
                    onChange={(event) => setSecret(event.target.value)}
                  />
                  <button
                    type="button"
                    className="channel-action-icon-btn"
                    title={showSecret ? "隐藏密码" : "显示密码"}
                    aria-label={showSecret ? "隐藏密码" : "显示密码"}
                    onClick={() => setShowSecret(!showSecret)}
                    tabIndex={-1}
                  >
                    {showSecret ? <EyeOffIcon /> : <EyeIcon />}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 运行策略卡片 */}
        <div className="channel-form-card">
          <div className="channel-form-card-header">
            <h3 className="channel-card-heading">运行与权限</h3>
            <p className="channel-card-subheading">配置{name}消息的默认执行模型、项目工作区和审批规则</p>
          </div>
          <div className="channel-form-card-body">
            <div className="channel-grid-fields">
              <div className="channel-field model-selector-field">
                <label>模型与思考强度</label>
                <ModelSelector
                  models={models}
                  selectedModelId={config.modelId}
                  selection={config.reasoningSelection ?? undefined}
                  disabled={busy}
                  onModelChange={(id) => setConfig((old) => ({ ...old, modelId: id, reasoningSelection: null }))}
                  onChange={(selection) => change("reasoningSelection", selection)}
                />
              </div>

              <div className="channel-field">
                <label htmlFor="field-project-id">执行位置</label>
                <div className="channel-select-wrapper">
                  <select
                    id="field-project-id"
                    value={config.projectId ?? ""}
                    onChange={(event) => change("projectId", event.target.value || null)}
                  >
                    <option value="">独立工作区</option>
                    {missingProject && <option value={config.projectId ?? ""}>已保存的项目不可用</option>}
                    {projects.map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="channel-field">
                <label htmlFor="field-approval-mode">审批模式</label>
                <div className="channel-select-wrapper">
                  <select
                    id="field-approval-mode"
                    value={config.approvalMode ?? "manual"}
                    onChange={(event) => {
                      const next = event.target.value as ChannelConfig["approvalMode"];
                      if (next === config.approvalMode) return;
                      if (next === "full_access") setShowFullAccessDialog(true);
                      else { change("approvalMode", next); setFullAccessConfirmed(false); }
                    }}
                  >
                    <option value="manual">请求审批</option>
                    <option value="auto_review">替我审批</option>
                    <option value="full_access">完全访问</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
        </div>

        {error && (
          <div className="channel-alert-banner channel-alert-banner--error" role="alert">
            <span className="channel-alert-icon">!</span>
            <span className="channel-alert-message">{error}</span>
          </div>
        )}

        <div className="channel-form-actions">
          <Button type="submit" variant="primary" size="medium" loading={busy} disabled={busy}>
            {busy ? "正在保存…" : "保存助理配置"}
          </Button>
        </div>
      </fieldset>
      <ConfirmDialog open={showFullAccessDialog} title="开启完全访问？" isDestructive busy={busy}
        confirmLabel="开启完全访问" cancelLabel="取消"
        description={<div className="channel-full-access-warning">
          <p>Eidos 将不再逐项请求批准</p>
          <p>此助理今后收到的消息都将以当前 macOS 用户的权限读写文件、运行命令和访问网络。操作可能造成数据丢失、凭据泄露或系统设置变化，也可能修改 Eidos 自身的数据。请仅在信任此助理及可向机器人发送消息的用户时开启。</p>
        </div>}
        onConfirm={() => { change("approvalMode", "full_access"); setFullAccessConfirmed(true); setShowFullAccessDialog(false); }}
        onCancel={() => setShowFullAccessDialog(false)} />
    </form>
  );
}
