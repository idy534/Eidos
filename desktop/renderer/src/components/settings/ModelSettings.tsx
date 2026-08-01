import React, { useEffect, useMemo, useRef, useState } from "react";
import type {
  ModelListResult,
  ModelOption,
  ModelProviderPreset,
  ModelPresetsResult,
} from "../../contracts.js";
import { Button } from "../Button.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
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

interface ModelDraft {
  originalId?: string;
  provider: ModelProviderPreset["id"];
  modelId: string;
  apiKey: string;
}

const vendorNames: Record<string, string> = {
  DeepSeek: "深度求索",
  MiniMax: "MiniMax",
  Kimi: "月之暗面",
};

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
    setDraft({ provider: provider.id, modelId: model.id, apiKey: "" });
  };

  const openEdit = (model: ModelOption) => {
    setLocalError(undefined);
    setDraft({
      originalId: model.id,
      provider: model.provider as ModelProviderPreset["id"],
      modelId: model.id,
      apiKey: "",
    });
  };

  async function save() {
    if (!draft || (!draft.originalId && !draft.apiKey.trim())) {
      setLocalError("API Key 必填");
      return;
    }
    setBusy(true);
    setLocalError(undefined);
    try {
      if (draft.originalId) {
        await window.eidosRuntime.updateModel({
          id: draft.originalId,
          provider: draft.provider,
          modelId: draft.modelId,
          ...(draft.apiKey.trim() ? { apiKey: draft.apiKey.trim() } : {}),
        });
      } else {
        await window.eidosRuntime.createModel({
          provider: draft.provider,
          modelId: draft.modelId,
          apiKey: draft.apiKey.trim(),
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

  async function remove(model: ModelOption) {
    setBusy(true);
    setLocalError(undefined);
    try {
      await window.eidosRuntime.deleteModel(model.id);
      await onModelsChanged();
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
        <p className="settings-panel-subtitle">自定义模型</p>
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
                  <span className={`model-vendor-icon model-vendor-icon--${model.provider}`} aria-hidden="true">
                    {model.vendor.slice(0, 1)}
                  </span>
                  <span className="saved-model-copy">
                    <strong>{model.name}</strong>
                    <span>{vendorNames[model.vendor] ?? model.vendor}</span>
                  </span>
                </div>
              }
              action={
                <div className="saved-model-actions">
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
                    onClick={() => void remove(model)}
                  >删除</Button>
                </div>
              }
            />
          ))
        ) : (
          <SettingRow title="尚未添加模型" description="添加后即可在 Session 的模型选择器中使用。" />
        )}
      </SettingSection>

      {(localError ?? modelError) && <p className="setting-field-error" role="alert">{localError ?? modelError}</p>}
      <ModelDialog
        draft={draft}
        presets={presets}
        busy={busy}
        error={localError}
        onChange={setDraft}
        onCancel={() => { if (!busy) { setDraft(undefined); setLocalError(undefined); } }}
        onSave={() => void save()}
      />
    </div>
  );
}

interface ModelDialogProps {
  draft?: ModelDraft | undefined;
  presets?: ModelPresetsResult | undefined;
  busy: boolean;
  error?: string | undefined;
  onChange: (draft: ModelDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}

function ModelDialog({ draft, presets, busy, error, onChange, onCancel, onSave }: ModelDialogProps) {
  const providerSelectRef = useRef<HTMLSelectElement>(null);
  useDialogFocusLifecycle({ open: Boolean(draft), initialFocusRef: providerSelectRef });
  const provider = useMemo(
    () => presets?.providers.find((item) => item.id === draft?.provider),
    [draft?.provider, presets],
  );

  useEffect(() => {
    if (!draft) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [busy, draft, onCancel]);

  if (!draft || !presets) return null;
  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="modal-dialog model-dialog" role="dialog" aria-modal="true" aria-labelledby="model-dialog-title" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header model-dialog-header">
          <div>
            <h3 id="model-dialog-title">{draft.originalId ? "编辑模型" : "添加模型"}</h3>
            <p>仅支持 OpenAI 兼容协议 API</p>
          </div>
        </div>
        <div className="modal-body model-dialog-fields">
          <label>
            <span>提供商</span>
            <select
              ref={providerSelectRef}
              aria-label="提供商"
              value={draft.provider}
              disabled={busy}
              onChange={(event) => {
                const next = presets.providers.find((item) => item.id === event.target.value)!;
                onChange({ ...draft, provider: next.id, modelId: next.models[0]?.id ?? "" });
              }}
            >
              {presets.providers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>
            <span>API Key</span>
            <input
              type="password"
              autoComplete="off"
              aria-label="API Key"
              placeholder={draft.originalId ? "留空表示保持原值" : "请输入 API Key"}
              value={draft.apiKey}
              disabled={busy}
              onChange={(event) => onChange({ ...draft, apiKey: event.target.value })}
            />
          </label>
          <label>
            <span>模型名称</span>
            <select
              aria-label="模型名称"
              value={draft.modelId}
              disabled={busy}
              onChange={(event) => onChange({ ...draft, modelId: event.target.value })}
            >
              {provider?.models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
            </select>
          </label>
          {error && <p className="setting-field-error" role="alert">{error}</p>}
        </div>
        <div className="modal-footer">
          <Button variant="ghost" disabled={busy} onClick={onCancel}>取消</Button>
          <Button variant="primary" loading={busy} disabled={busy} onClick={onSave}>保存</Button>
        </div>
      </div>
    </div>
  );
}
