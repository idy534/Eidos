import { useEffect, useMemo, useRef, useState } from "react";
import type { ModelProviderPreset, ModelPresetsResult } from "../../contracts.js";
import type { CustomModelSettings } from "../../../../shared/custom-models.js";
import { Button } from "../Button.js";
import { Tooltip } from "../Tooltip.js";
import { ProviderLogo } from "../ProviderLogo.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import { CustomModelFields } from "./CustomModelFields.js";
import { EyeIcon, EyeOffIcon } from "./ChannelIcons.js";

export interface ModelDraft {
  originalId?: string;
  provider: ModelProviderPreset["id"] | "custom";
  modelId: string;
  apiKey: string;
  apiKeyEdited?: boolean;
  originalBaseUrl?: string;
  custom?: CustomModelSettings;
}

export const DEFAULT_CUSTOM_CONTEXT_WINDOW_TOKENS = 128_000;
export const DEFAULT_CUSTOM_MAX_OUTPUT_TOKENS = 8_192;

export function emptyCustomSettings(): CustomModelSettings {
  return { providerId: "", providerName: "", baseUrl: "", wireApi: "chat_completions", name: "",
    contextWindowTokens: 0, maxOutputTokens: 0, supportsToolCall: true, supportsImages: false,
    reasoning: null, reasoningFormat: "openai", reasoningControl: "provider_default" };
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

export function ModelDialog({ draft, presets, busy, error, onChange, onCancel, onSave }: ModelDialogProps) {
  const [showKey, setShowKey] = useState(false);
  const providerSelectRef = useRef<HTMLSelectElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocusLifecycle({ open: Boolean(draft), initialFocusRef: providerSelectRef });
  const provider = useMemo(
    () => presets?.providers.find((item) => item.id === draft?.provider),
    [draft?.provider, presets],
  );

  useEffect(() => {
    if (!draft) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onCancel();
      if (event.key === "Tab") {
        const controls = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]') ?? [])
          .filter(element => element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [busy, draft, onCancel]);

  if (!draft || !presets) return null;
  return (
    <div className="modal-backdrop">
      <div ref={dialogRef} className={`modal-dialog model-dialog${draft.custom ? " model-dialog--custom" : ""}`} role="dialog" aria-modal="true" aria-labelledby="model-dialog-title" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header model-dialog-header">
          <div>
            <h3 id="model-dialog-title">{draft.originalId ? "编辑模型" : "添加模型"}</h3>
            {!draft.custom && <p>仅支持 OpenAI 兼容协议 API</p>}
          </div>
        </div>
        <div className="modal-body model-dialog-fields">
          <label>
            <span>提供商</span>
            <div className="model-select-control model-select-control--with-prefix model-provider-control">
              <span className="model-select-control__prefix" aria-hidden="true">
                <ProviderLogo provider={draft.provider} className="model-provider-control__logo" />
              </span>
              <select
                ref={providerSelectRef}
                aria-label="提供商"
                value={draft.provider}
                disabled={busy || Boolean(draft.originalId && draft.custom)}
                onChange={(event) => {
                  if (event.target.value === "custom") {
                    onChange({ ...(draft.originalId ? { originalId: draft.originalId } : {}), provider: "custom", modelId: "", apiKey: "", custom: emptyCustomSettings() });
                    return;
                  }
                  const next = presets.providers.find((item) => item.id === event.target.value)!;
                  onChange({ ...(draft.originalId ? { originalId: draft.originalId } : {}), provider: next.id, modelId: next.models[0]?.id ?? "", apiKey: draft.apiKey });
                }}
              >
                {presets.providers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                <option value="custom">自定义（OpenAI 兼容）</option>
              </select>
              <span className="model-select-control__chevron" aria-hidden="true">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m6 9 6 6 6-6" />
                </svg>
              </span>
            </div>
          </label>
          {draft.custom ? <CustomModelFields key={draft.originalId ?? "new"} draft={draft} busy={busy} onChange={onChange} /> : <>
          <label>
            <span>API Key</span>
            <div className="custom-model-secret">
              <div className="custom-model-secret-input">
                <input
                  type={showKey ? "text" : "password"}
                  autoComplete="off"
                  aria-label="API Key"
                  placeholder="请输入 API Key"
                  value={draft.apiKey}
                  disabled={busy}
                  onChange={(event) => onChange({ ...draft, apiKey: event.target.value })}
                />
                <Tooltip content={showKey ? "隐藏 API Key" : "显示 API Key"}>
                  <Button
                    type="button"
                    variant="ghost"
                    size="small"
                    className="custom-model-secret-toggle"
                    aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
                    title={showKey ? "隐藏 API Key" : "显示 API Key"}
                    aria-pressed={showKey}
                    disabled={busy}
                    onClick={() => setShowKey(!showKey)}
                  >
                    {showKey ? <EyeOffIcon /> : <EyeIcon />}
                  </Button>
                </Tooltip>
              </div>
            </div>
          </label>
          <label>
            <span>模型名称</span>
            <div className="model-select-control">
              <select
                aria-label="模型名称"
                value={draft.modelId}
                disabled={busy}
                onChange={(event) => onChange({ ...draft, modelId: event.target.value })}
              >
                {provider?.models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
              </select>
              <span className="model-select-control__chevron" aria-hidden="true">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m6 9 6 6 6-6" />
                </svg>
              </span>
            </div>
          </label>
          </>}
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
