import React, { useState } from "react";
import type { ModelListResult, ModelStatus } from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { SettingSection } from "./SettingSection";
import { SettingRow } from "./SettingRow";
import { StatusBadge } from "./StatusBadge";

interface ModelSettingsProps {
  model?: ModelStatus | undefined;
  modelList?: ModelListResult | undefined;
  pendingAction: SettingsPendingAction;
  storageHealthReady: boolean;
  onConfigureModel: (apiKey: string) => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}

export function ModelSettings({
  model,
  modelList,
  pendingAction,
  storageHealthReady,
  onConfigureModel,
  onShowToast,
}: ModelSettingsProps) {
  const [editingKey, setEditingKey] = useState(false);
  const [inputKey, setInputKey] = useState("");
  const [localError, setLocalError] = useState<string>();

  const isSaving = pendingAction?.type === "configure_model";

  async function handleSave() {
    if (inputKey.length < 16) {
      setLocalError("API Key 长度不能小于 16 位字符");
      return;
    }
    setLocalError(undefined);
    try {
      await onConfigureModel(inputKey);
      setInputKey("");
      setEditingKey(false);
      onShowToast("API Key 保存成功", "success");
    } catch (cause) {
      const msg = cause instanceof Error ? cause.message : "保存 API Key 失败";
      setLocalError(msg);
    }
  }

  function handleCancel() {
    setInputKey("");
    setEditingKey(false);
    setLocalError(undefined);
  }

  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <h1>模型与 API</h1>
        <p className="settings-panel-subtitle">
          配置 Eidos 用于执行任务的模型服务。支持的模型由 Runtime 返回，任务启动后锁定本次使用的模型。
        </p>
      </div>

      <SettingSection
        title="DeepSeek 模型服务"
        description="系统默认的 LLM 执行引擎与功能推理模型。"
      >
        {modelList?.models.map((option) => {
          const isDefault = option.id === modelList.defaultModelId;
          return (
            <SettingRow
              key={option.id}
              title={
                <div className="model-row-title">
                  <span className="model-name">{option.displayName}</span>
                  <code className="model-id">{option.id}</code>
                </div>
              }
              action={
                <div className="model-row-badges">
                  {isDefault && <StatusBadge tone="info" dot={false}>默认</StatusBadge>}
                  {option.configured ? (
                    <StatusBadge tone="success">可用</StatusBadge>
                  ) : (
                    <StatusBadge tone="warning">待配置</StatusBadge>
                  )}
                </div>
              }
            />
          );
        })}
      </SettingSection>

      <SettingSection
        title="API 凭证"
        description="用于认证 DeepSeek API 的私钥凭证。凭证仅保存在本机 ~/.eidos/model.json（权限 0600），不会写入项目。"
      >
        <SettingRow
          title="DeepSeek API Key"
          description={
            model?.configured
              ? "凭证已保存在仅当前用户可读的本地配置中（~/.eidos/model.json）"
              : "尚未配置有效的 API Key，请填入凭证后启动 Agent"
          }
          action={
            !editingKey && (
              <button
                type="button"
                className="button-secondary"
                disabled={!storageHealthReady || isSaving}
                onClick={() => {
                  setEditingKey(true);
                  setLocalError(undefined);
                }}
              >
                {model?.configured ? "更新凭证" : "配置 API Key"}
              </button>
            )
          }
        >
          {editingKey ? (
            <div className="api-key-edit-form">
              <div className="api-key-input-row">
                <input
                  type="password"
                  autoComplete="off"
                  placeholder="sk-…"
                  value={inputKey}
                  disabled={isSaving}
                  onChange={(e) => setInputKey(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && inputKey.length >= 16 && !isSaving) {
                      e.preventDefault();
                      void handleSave();
                    }
                  }}
                />
                <button
                  type="button"
                  className="button-primary"
                  disabled={isSaving || !storageHealthReady || inputKey.length < 16}
                  onClick={() => void handleSave()}
                >
                  {isSaving ? "保存中…" : "保存配置"}
                </button>
                <button
                  type="button"
                  className="button-ghost"
                  disabled={isSaving}
                  onClick={handleCancel}
                >
                  取消
                </button>
              </div>
              {localError && <p className="setting-field-error" role="alert">{localError}</p>}
            </div>
          ) : (
            <div className="api-key-masked-display">
              <code>{model?.configured ? "••••••••••••••••••••••••••••••••" : "未配置"}</code>
            </div>
          )}
        </SettingRow>
      </SettingSection>
    </div>
  );
}
