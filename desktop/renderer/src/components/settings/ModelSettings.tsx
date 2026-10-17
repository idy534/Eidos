import { customModelAddressError } from "./custom-model-address.js";
import React, { useEffect, useState } from "react";
import type {
  ModelListResult,
  ModelOption,
  ModelPresetsResult,
} from "../../contracts.js";
import { Button } from "../Button.js";
import { getProviderName, ProviderLogo } from "../ProviderLogo.js";
import {
  DEFAULT_CUSTOM_CONTEXT_WINDOW_TOKENS,
  DEFAULT_CUSTOM_MAX_OUTPUT_TOKENS,
  emptyCustomSettings,
  ModelDialog,
  type ModelDraft,
} from "./ModelDialog.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { SettingSection } from "./SettingSection.js";
import { SettingRow } from "./SettingRow.js";

interface ModelSettingsProps {
  modelList?: ModelListResult | undefined;
  modelLoading?: boolean | undefined;
  modelError?: string | undefined;
  storageHealthReady: boolean;
  onModelsChanged: () => Promise<void>;
  onShowToast: (message: string, type: "success" | "info" | "error") => void;
}


export function ModelSettings({
  modelList,
  modelLoading,
  modelError,
  storageHealthReady,
  onModelsChanged,
  onShowToast,
}: ModelSettingsProps) {
  const [presets, setPresets] = useState<ModelPresetsResult | undefined>(undefined);
  const [draft, setDraft] = useState<ModelDraft | undefined>(undefined);
  const [modelToDelete, setModelToDelete] = useState<ModelOption | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>(undefined);

  useEffect(() => {
    let active = true;
    void window.eidosRuntime.listModelPresets().then((result) => {
      if (active) setPresets(result);
    }).catch(() => {
      if (active) setLocalError("内置模型目录加载失败");
    });
    return () => { active = false; };
  }, []);

  const openCreate = () => {
    const provider = presets?.providers[0];
    const model = provider?.models[0];
    if (!provider || !model) return;
    setLocalError(undefined);
    setModelToDelete(undefined);
    setDraft({ provider: provider.id, modelId: model.id, apiKey: "" });
  };

  const openEdit = (model: ModelOption) => {
    setLocalError(undefined);
    setModelToDelete(undefined);
    setDraft({
      originalId: model.id,
      provider: model.custom ? "custom" : model.provider as ModelDraft["provider"],
      modelId: model.modelId ?? model.id,
      ...(model.custom ? { custom: { ...model.custom }, originalBaseUrl: model.custom.baseUrl } : {}),
      apiKey: "",
    });
  };
  const openCopy = (model: ModelOption) => {
    if (!model.custom) return;
    setLocalError(undefined);
    setModelToDelete(undefined);
    setDraft({
      provider: "custom",
      modelId: "",
      apiKey: "",
      custom: {
        ...emptyCustomSettings(),
        ...(model.custom.providerId ? { providerId: model.custom.providerId } : {}),
        providerName: model.custom.providerName,
        baseUrl: model.custom.baseUrl,
        ...(model.custom.wireApi ? { wireApi: model.custom.wireApi } : {}),
      },
    });
  };

  async function save() {
    if (!draft || (!draft.custom && !draft.originalId && !draft.apiKey.trim())) {
      setLocalError("API Key 必填");
      return;
    }
    if (draft.custom && !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(draft.custom.providerId ?? "")) {
      setLocalError("请填写提供商 ID，只能使用字母、数字、点、下划线和短横线，并以字母或数字开头。");
      return;
    }
    const resolveTokenLimit = (value: number | undefined, defaultValue: number): number => {
      if (value === undefined || value === null || value === 0) {
        return defaultValue;
      }
      return value;
    };

    const resolvedCustom = draft.custom ? {
      ...draft.custom,
      contextWindowTokens: resolveTokenLimit(draft.custom.contextWindowTokens, DEFAULT_CUSTOM_CONTEXT_WINDOW_TOKENS),
      maxOutputTokens: resolveTokenLimit(draft.custom.maxOutputTokens, DEFAULT_CUSTOM_MAX_OUTPUT_TOKENS),
    } : undefined;

    if (resolvedCustom && (!draft.modelId.trim() || !resolvedCustom.baseUrl.trim() || !resolvedCustom.name.trim()
      || !resolvedCustom.providerName.trim() || !Number.isSafeInteger(resolvedCustom.contextWindowTokens) || !Number.isSafeInteger(resolvedCustom.maxOutputTokens)
      || resolvedCustom.contextWindowTokens <= 0 || resolvedCustom.maxOutputTokens <= 0
      || resolvedCustom.contextWindowTokens > 100000000 || resolvedCustom.maxOutputTokens > 100000000
      || resolvedCustom.maxOutputTokens > resolvedCustom.contextWindowTokens)) {
      setLocalError("请填写模型 ID、展示名、接口地址和有效的 Token 上限；最大输出不能超过上下文窗口。");
      return;
    }
    if (resolvedCustom) {
      const addressError = customModelAddressError(resolvedCustom.baseUrl);
      if (addressError) { setLocalError(addressError); return; }
    }
    setBusy(true);
    setLocalError(undefined);
    try {
      const extra = resolvedCustom ? { custom: resolvedCustom } : {};
      const keyChanged = draft.apiKeyEdited || Boolean(draft.apiKey.trim())
        || (resolvedCustom && draft.originalBaseUrl !== resolvedCustom.baseUrl);
      if (draft.originalId) {
        await window.eidosRuntime.updateModel({
          id: draft.originalId,
          provider: draft.provider,
          modelId: draft.modelId,
          ...extra,
          ...(keyChanged ? { apiKey: draft.apiKey.trim() } : {}),
        });
      } else {
        await window.eidosRuntime.createModel({
          provider: draft.provider,
          modelId: draft.modelId,
          apiKey: draft.apiKey.trim(),
          ...extra,
        });
      }
      await onModelsChanged();
      setDraft(undefined);
      onShowToast("模型已保存", "success");
    } catch {
      setLocalError("模型保存失败，请检查配置或 Runtime 日志。");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!modelToDelete) return;
    setBusy(true);
    setLocalError(undefined);
    try {
      await window.eidosRuntime.deleteModel(modelToDelete.id);
      await onModelsChanged();
      setModelToDelete(undefined);
      onShowToast("模型已删除", "success");
    } catch {
      setLocalError("模型删除失败，请查看 Runtime 日志。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-panel model-settings-panel">
      <div className="settings-panel-header">
        <h1>模型</h1>
      </div>

      <SettingSection title="">
        <SettingRow
          title="本地配置文件"
          description="管理写入 ~/.eidos/models.json"
          action={
            <Button variant="primary" disabled={!storageHealthReady || !presets || busy} onClick={openCreate}>
              添加模型
            </Button>
          }
        />
      </SettingSection>

      <SettingSection title="已保存模型">
        {modelLoading ? (
          <SettingRow title="正在加载模型…" />
        ) : modelList?.models.length ? (
          modelList.models.map((model) => (
            <SettingRow
              key={model.id}
              title={
                <div className="saved-model">
                  <ProviderLogo provider={model.provider} className="model-vendor-icon" />
                  <span className="saved-model-copy">
                    <strong>{model.name}</strong>
                    <span>{model.custom ? model.vendor : getProviderName(model.provider)}</span>
                  </span>
                </div>
              }
              action={
                <div className="saved-model-actions">
                  {model.custom && (
                    <Button
                      size="small"
                      variant="secondary"
                      aria-label={`复制 ${model.name}`}
                      disabled={busy}
                      onClick={() => openCopy(model)}
                    >复制</Button>
                  )}
                  <Button
                    size="small"
                    variant="secondary"
                    aria-label={`编辑 ${model.name}`}
                    disabled={busy}
                    onClick={() => openEdit(model)}
                  >编辑</Button>
                  <Button
                    size="small"
                    variant="ghost"
                    aria-label={`删除 ${model.name}`}
                    disabled={busy}
                    onClick={() => {
                      setLocalError(undefined);
                      setModelToDelete(model);
                    }}
                  >删除</Button>
                </div>
              }
            />
          ))
        ) : (
          <SettingRow title="尚未添加模型" description="添加后即可在 Session 的模型选择器中使用。" />
        )}
      </SettingSection>

      {(localError ?? modelError) && !modelToDelete && !draft && (
        <p className="setting-field-error" role="alert">{localError ?? modelError}</p>
      )}
      <ModelDialog
        draft={draft}
        presets={presets}
        busy={busy}
        error={localError}
        onChange={setDraft}
        onCancel={() => { if (!busy) { setDraft(undefined); setLocalError(undefined); } }}
        onSave={() => void save()}
      />
      <ConfirmDialog
        open={Boolean(modelToDelete)}
        title={`删除模型 “${modelToDelete?.name ?? ""}”？`}
        description={`确定要删除模型 “${modelToDelete?.name ?? ""}” 吗？删除后该模型将从本地配置中移除，无法在会话中继续使用。`}
        confirmLabel="删除"
        cancelLabel="取消"
        isDestructive
        busy={busy}
        error={localError}
        onConfirm={() => void confirmDelete()}
        onCancel={() => {
          if (!busy) {
            setModelToDelete(undefined);
            setLocalError(undefined);
          }
        }}
      />
    </div>
  );
}
