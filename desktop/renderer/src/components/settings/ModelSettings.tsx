import React, { useEffect, useState } from "react";
import { Button } from "../Button.js";
import type {
  ModelListResult,
  ModelProfile,
  ModelProfileDraft,
  ModelStatus,
  ModelTestConnectionResult,
  WireAPI,
} from "../../contracts";
import type { SettingsPendingAction } from "./settings-types";
import { SettingSection } from "./SettingSection";
import { SettingRow } from "./SettingRow";
import { StatusBadge } from "./StatusBadge";

interface ModelSettingsProps {
  model?: ModelStatus | undefined;
  modelList?: ModelListResult | undefined;
  modelLoading?: boolean | undefined;
  modelError?: string | undefined;
  modelConfiguring: boolean;
  storageHealthReady: boolean;
  onConfigureModel: (apiKey: string) => Promise<boolean>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}

export function ModelSettings({
  model,
  modelList,
  modelLoading,
  modelError,
  modelConfiguring,
  storageHealthReady,
  onConfigureModel,
  onShowToast,
}: ModelSettingsProps) {
  const [editingKey, setEditingKey] = useState(false);
  const [inputKey, setInputKey] = useState("");
  const [localError, setLocalError] = useState<string>();
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [editingProfileId, setEditingProfileId] = useState<string>();
  const [profileDraft, setProfileDraft] = useState<ModelProfileDraft>({
    name: "",
    provider: "deepseek",
    modelId: "",
    contextWindow: 128000,
    maxOutputTokens: 4096,
    requestTimeout: 120,
    retryPolicy: { maxAttempts: 3 },
  });
  const [profileKey, setProfileKey] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);
  const [probeResults, setProbeResults] = useState<Record<string, ModelTestConnectionResult>>({});

  async function loadProfiles() {
    if (!window.eidosRuntime.listModelProfiles) return;
    setProfiles(await window.eidosRuntime.listModelProfiles());
  }

  useEffect(() => {
    void loadProfiles().catch(() => setLocalError("Model Profile 加载失败"));
  }, []);

  async function saveProfile() {
    if (!profileDraft.name.trim() || !profileDraft.modelId.trim()) {
      setLocalError("Profile 名称和 Model ID 必填");
      return;
    }
    if (!editingProfileId && !profileKey && !profileDraft.authReference) {
      setLocalError("首次保存需填写 API Key 或环境变量引用");
      return;
    }
    setProfileBusy(true);
    setLocalError(undefined);
    try {
      if (editingProfileId) {
        await window.eidosRuntime.updateModelProfile(
          editingProfileId,
          profileDraft,
          profileKey || undefined,
        );
      } else {
        await window.eidosRuntime.createModelProfile(
          profileDraft,
          profileKey || undefined,
        );
      }
      setProfileKey("");
      setEditingProfileId(undefined);
      setProfileDraft({
        name: "",
        provider: "deepseek",
        modelId: "",
        contextWindow: 128000,
        maxOutputTokens: 4096,
        requestTimeout: 120,
        retryPolicy: { maxAttempts: 3 },
      });
      await loadProfiles();
      onShowToast("Model Profile 已保存", "success");
    } catch {
      setLocalError("Model Profile 保存失败，请查看 Runtime 日志。");
    } finally {
      setProfileBusy(false);
    }
  }

  function editProfile(profile: ModelProfile) {
    setEditingProfileId(profile.id);
    setProfileKey("");
    setProfileDraft({
      name: profile.name,
      provider: profile.provider,
      baseUrl: profile.baseUrl ?? undefined,
      authReference: profile.authReference.startsWith("env:")
        ? profile.authReference
        : undefined,
      wireApi: profile.wireApi,
      modelId: profile.modelId,
      contextWindow: profile.contextWindow ?? undefined,
      maxOutputTokens: profile.maxOutputTokens ?? undefined,
      reasoningMode: profile.reasoningMode,
      supportsTools: profile.supportsTools ?? undefined,
      supportsParallelTools: profile.supportsParallelTools ?? undefined,
      supportsImages: profile.supportsImages ?? undefined,
      supportsStructuredOutput: profile.supportsStructuredOutput ?? undefined,
      supportsPromptCache: profile.supportsPromptCache ?? undefined,
      requestTimeout: profile.requestTimeout,
      retryPolicy: profile.retryPolicy,
    });
  }

  async function testProfile(profileId: string) {
    setProfileBusy(true);
    try {
      const result = await window.eidosRuntime.testModelProfile(profileId);
      setProbeResults((current) => ({ ...current, [profileId]: result }));
      await loadProfiles();
    } catch {
      setLocalError("Test Connection 失败，请查看 Runtime 日志。");
    } finally {
      setProfileBusy(false);
    }
  }

  const isSaving = modelConfiguring;
  const effectiveError = localError ?? modelError;

  async function handleSave() {
    if (inputKey.length < 16) {
      setLocalError("API Key 长度不能小于 16 位字符");
      return;
    }
    setLocalError(undefined);
    try {
      const success = await onConfigureModel(inputKey);
      if (success) {
        setInputKey("");
        setEditingKey(false);
        onShowToast("API Key 保存成功", "success");
      }
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

  const apiKeyDescription = modelLoading
    ? "正在加载模型配置…"
    : model?.configured
      ? "凭证已保存在仅当前用户可读的本地配置中（~/.eidos/model.json）"
      : "尚未配置有效的 API Key，请填入凭证后启动 Agent";

  return (
    <div className="settings-panel">
      <div className="settings-panel-header">
        <h1>模型与 API</h1>
        <p className="settings-panel-subtitle">
          配置 Eidos 用于执行任务的模型服务。支持的模型由 Runtime 返回，任务启动后锁定本次使用的模型。
        </p>
      </div>

      <SettingSection
        title="Model Profiles"
        description="Provider 配置、协议和验证结果彼此独立；任务启动后冻结本次 Profile 与 Capability Snapshot。"
      >
        {profiles.map((profile) => {
          const result = probeResults[profile.id];
          return (
            <SettingRow
              key={profile.id}
              title={
                <div className="model-row-title">
                  <span className="model-name">{profile.name}</span>
                  <code className="model-id">{profile.modelId}</code>
                </div>
              }
              description={`${profile.provider} · ${profile.wireApi}`}
              action={
                <div className="model-row-badges">
                  <StatusBadge tone={result?.success ? "success" : "neutral"}>
                    {result?.success ? "Verified" : "Unknown"}
                  </StatusBadge>
                  <Button size="small" variant="secondary" disabled={profileBusy} onClick={() => editProfile(profile)}>
                    编辑
                  </Button>
                  <Button size="small" variant="secondary" disabled={profileBusy} onClick={() => void testProfile(profile.id)}>
                    Test Connection
                  </Button>
                  <Button
                    size="small"
                    variant="ghost"
                    disabled={profileBusy}
                    onClick={() => void window.eidosRuntime.deleteModelProfile(profile.id).then(loadProfiles)}
                  >
                    删除
                  </Button>
                </div>
              }
            >
              <p className="setting-row-description">
                Declared: Tools {profile.supportsTools === true ? "Yes" : profile.supportsTools === false ? "No" : "Unknown"}
                {" · "}Structured Output {profile.supportsStructuredOutput === true ? "Yes" : profile.supportsStructuredOutput === false ? "No" : "Unknown"}
              </p>
              {result?.capabilitySnapshot && (
                <p className="setting-row-description">
                  Verified: Tools {result.capabilitySnapshot.supportsTools ? "Supported" : "Unsupported"}
                  {" · "}Structured Output {result.capabilitySnapshot.supportsStructuredOutput ? "Supported" : "Unsupported"}
                </p>
              )}
              {result?.error && <p role="alert">{result.error.code}: {result.error.message}</p>}
            </SettingRow>
          );
        })}
        <SettingRow title={editingProfileId ? "编辑 Model Profile" : "新建 Model Profile"}>
          <div className="api-key-edit-form">
            <div className="api-key-input-row">
              <input aria-label="Profile 名称" placeholder="Profile 名称" value={profileDraft.name} onChange={(event) => setProfileDraft({ ...profileDraft, name: event.target.value })} />
              <select aria-label="Provider preset" value={profileDraft.provider} onChange={(event) => setProfileDraft({ ...profileDraft, provider: event.target.value })}>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="deepseek">DeepSeek</option>
                <option value="volcengine_ark">火山方舟</option>
                <option value="minimax">MiniMax</option>
                <option value="moonshot">Kimi / Moonshot</option>
                <option value="qwen">Qwen / DashScope</option>
                <option value="custom_openai_compatible">Custom OpenAI-compatible</option>
              </select>
              <input aria-label="Model ID" placeholder="Model ID" value={profileDraft.modelId} onChange={(event) => setProfileDraft({ ...profileDraft, modelId: event.target.value })} />
              <select aria-label="Wire API" value={profileDraft.wireApi ?? ""} onChange={(event) => setProfileDraft({ ...profileDraft, wireApi: (event.target.value || undefined) as WireAPI | undefined })}>
                <option value="">Preset 默认协议</option>
                <option value="openai_responses">OpenAI Responses</option>
                <option value="anthropic_messages">Anthropic Messages</option>
                <option value="openai_chat_completions">OpenAI Chat Completions</option>
              </select>
            </div>
            <div className="api-key-input-row">
              <input aria-label="Base URL" placeholder="自定义 Base URL（可选）" value={profileDraft.baseUrl ?? ""} onChange={(event) => setProfileDraft({ ...profileDraft, baseUrl: event.target.value || undefined })} />
              <input type="password" autoComplete="off" aria-label="Profile API Key" placeholder={editingProfileId ? "新 API Key（留空不变）" : "API Key"} value={profileKey} onChange={(event) => setProfileKey(event.target.value)} />
              <input aria-label="Auth environment reference" placeholder="env:OPENAI_API_KEY（可选）" value={profileDraft.authReference ?? ""} onChange={(event) => setProfileDraft({ ...profileDraft, authReference: event.target.value || undefined })} />
            </div>
            <div className="api-key-input-row">
              <label><input type="checkbox" checked={profileDraft.supportsTools ?? false} onChange={(event) => setProfileDraft({ ...profileDraft, supportsTools: event.target.checked })} /> Declared Tools</label>
              <label><input type="checkbox" checked={profileDraft.supportsStructuredOutput ?? false} onChange={(event) => setProfileDraft({ ...profileDraft, supportsStructuredOutput: event.target.checked })} /> Declared Structured Output</label>
              <input type="number" aria-label="Timeout seconds" min="1" max="600" value={profileDraft.requestTimeout ?? 120} onChange={(event) => setProfileDraft({ ...profileDraft, requestTimeout: Number(event.target.value) })} />
              <input type="number" aria-label="Retry attempts" min="1" max="10" value={profileDraft.retryPolicy?.maxAttempts ?? 3} onChange={(event) => setProfileDraft({ ...profileDraft, retryPolicy: { ...profileDraft.retryPolicy, maxAttempts: Number(event.target.value) } })} />
              <Button variant="primary" disabled={profileBusy || !storageHealthReady} loading={profileBusy} onClick={() => void saveProfile()}>
                {editingProfileId ? "保存 Profile" : "创建 Profile"}
              </Button>
            </div>
          </div>
        </SettingRow>
      </SettingSection>

      <SettingSection
        title="DeepSeek 模型服务"
        description="系统默认的 LLM 执行引擎与功能推理模型。"
      >
        {modelLoading ? (
          <SettingRow
            title="模型列表"
            description="正在从 Local Runtime 获取可用模型列表…"
            action={<StatusBadge tone="neutral">加载中</StatusBadge>}
          />
        ) : (
          modelList?.models.map((option) => {
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
          })
        )}
      </SettingSection>

      <SettingSection
        title="API 凭证"
        description="用于认证 DeepSeek API 的私钥凭证。凭证仅保存在本机 ~/.eidos/model.json（权限 0600），不会写入项目。"
      >
        <SettingRow
          title="DeepSeek API Key"
          description={apiKeyDescription}
          action={
            !editingKey && (
              <Button
                variant="secondary"
                size="medium"
                disabled={!storageHealthReady || isSaving || modelLoading}
                onClick={() => {
                  setEditingKey(true);
                  setLocalError(undefined);
                }}
              >
                {model?.configured ? "更新凭证" : "配置 API Key"}
              </Button>
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
                <Button
                  variant="primary"
                  size="medium"
                  disabled={isSaving || !storageHealthReady || inputKey.length < 16}
                  loading={isSaving}
                  onClick={() => void handleSave()}
                >
                  {isSaving ? "保存中…" : "保存配置"}
                </Button>
                <Button
                  variant="ghost"
                  size="medium"
                  disabled={isSaving}
                  onClick={handleCancel}
                >
                  取消
                </Button>
              </div>
              {effectiveError && <p className="setting-field-error" role="alert">{effectiveError}</p>}
            </div>
          ) : (
            <div className="api-key-masked-display">
              <code>{model?.configured ? "••••••••••••••••••••••••••••••••" : "未配置"}</code>
              {effectiveError && <p className="setting-field-error" role="alert">{effectiveError}</p>}
            </div>
          )}
        </SettingRow>
      </SettingSection>
    </div>
  );
}
