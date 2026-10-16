import React, { useState } from "react";
import type { ChannelConfig, ChannelPublicConnection, ChannelSaveRequest, State } from "../../../../shared/channels.js";
import type { ModelOption, Project } from "../../contracts.js";
import { Button } from "../Button.js";
import { StatusBadge, type StatusTone } from "./StatusBadge.js";
import { Toggle } from "./Toggle.js";
import { BackArrowIcon, DingTalkLogo, EyeIcon, EyeOffIcon, RefreshIcon } from "./ChannelIcons.js";

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

export function DingTalkForm({
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
  const [config, setConfig] = useState<ChannelConfig>(connection?.config ?? {
    enabled: false, clientId: "", modelId: models[0]?.id ?? "",
    projectId: null, approvalMode: "manual",
  });
  const [secret, setSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [error, setError] = useState("");

  const change = <K extends keyof ChannelConfig>(key: K, value: ChannelConfig[K]) => setConfig((old) => ({ ...old, [key]: value }));
  const missingModel = config.modelId && !models.some((model) => model.id === config.modelId);
  const missingProject = config.projectId && !projects.some((project) => project.id === config.projectId);

  return (
    <form
      className="channel-form"
      onSubmit={(event) => {
        event.preventDefault();
        setError("");
        if (!secret && (!connection?.hasSecret || config.clientId !== connection.config.clientId)) {
          setError("请填写 Client Secret。 ");
          return;
        }
        void onSave({
          expectedRevision: connection?.revision ?? 0,
          config,
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
                aria-label="接入渠道"
              >
                <BackArrowIcon />
                <span>接入渠道</span>
              </button>
              <div className="channel-topbar-divider" />
            </>
          )}
          <div className="channel-topbar-identity">
            <div className="channel-topbar-logo">
              <DingTalkLogo size={24} />
            </div>
            <h2 className="channel-topbar-title">钉钉</h2>
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
            title={config.enabled ? "点击停用钉钉渠道" : "点击启用钉钉渠道"}
          >
            <Toggle
              id="field-channel-enabled"
              label="启用钉钉渠道"
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
        <legend className="sr-only">钉钉连接配置</legend>

        {/* 应用凭证卡片 */}
        <div className="channel-form-card">
          <div className="channel-form-card-header">
            <h3 className="channel-card-heading">应用凭证</h3>
            <p className="channel-card-subheading">在钉钉开放平台创建的企业内部应用凭证（需启用 Stream 模式）</p>
          </div>
          <div className="channel-form-card-body">
            <div className="channel-grid-fields">
              <div className="channel-field">
                <label htmlFor="field-client-id">Client ID</label>
                <input
                  id="field-client-id"
                  required
                  maxLength={256}
                  value={config.clientId}
                  autoComplete="off"
                  placeholder="请输入 AppKey / Client ID"
                  onChange={(event) => change("clientId", event.target.value.trim())}
                />
              </div>

              <div className="channel-field">
                <label htmlFor="field-client-secret">Client Secret</label>
                <div className="channel-input-with-action">
                  <input
                    id="field-client-secret"
                    type={showSecret ? "text" : "password"}
                    maxLength={1024}
                    value={secret}
                    autoComplete="new-password"
                    placeholder={connection?.hasSecret ? "留空保留已保存的凭证" : "请输入应用凭证 (AppSecret)"}
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
            <p className="channel-card-subheading">配置钉钉消息的默认执行模型、项目工作区和审批规则</p>
          </div>
          <div className="channel-form-card-body">
            <div className="channel-grid-fields">
              <div className="channel-field">
                <label htmlFor="field-model-id">模型</label>
                <div className="channel-select-wrapper">
                  <select
                    id="field-model-id"
                    required
                    value={config.modelId}
                    onChange={(event) => change("modelId", event.target.value)}
                  >
                    <option value="">请选择模型</option>
                    {missingModel && <option value={config.modelId}>已保存的模型不可用</option>}
                    {models.map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.name}
                      </option>
                    ))}
                  </select>
                </div>
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
                    onChange={(event) => change("approvalMode", event.target.value as "manual" | "auto_review")}
                  >
                    <option value="manual">请求审批</option>
                    <option value="auto_review">替我审批</option>
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
            {busy ? "正在保存…" : "保存渠道配置"}
          </Button>
        </div>
      </fieldset>
    </form>
  );
}
