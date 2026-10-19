import { customModelAddressError } from "./custom-model-address.js";
import { useEffect, useRef, useState } from "react";
import type { CustomModelSettings, CustomProviderResult, DiscoveredModel } from "../../../../shared/custom-models.js";
import type { ModelDraft } from "./ModelDialog.js";
import { Button } from "../Button.js";
import { Tooltip } from "../Tooltip.js";
import { ModelIdCombobox } from "./ModelIdCombobox.js";
import { CustomReasoningFields } from "./CustomReasoningFields.js";
import { TokenLimitField } from "./TokenLimitField.js";
import { EyeIcon, EyeOffIcon } from "./ChannelIcons.js";

type CustomField = keyof CustomModelSettings;
interface Props { draft: ModelDraft; busy: boolean; onChange: (draft: ModelDraft) => void; }

export function CustomModelFields({ draft, busy, onChange }: Props) {
  const custom = draft.custom!;
  const [result, setResult] = useState<CustomProviderResult>();
  const [testing, setTesting] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [source, setSource] = useState<string>();
  const edited = useRef(new Set<CustomField>());
  const requestVersion = useRef(0);
  useEffect(() => {
    if (!testing) { setShowProgress(false); return; }
    const timer = window.setTimeout(() => setShowProgress(true), 200);
    return () => window.clearTimeout(timer);
  }, [testing]);
  useEffect(() => {
    requestVersion.current += 1;
    setResult(undefined); setTesting(false);
    return () => { requestVersion.current += 1; };
  }, [custom.baseUrl, draft.apiKey, draft.apiKeyEdited]);

  const update = <K extends CustomField>(key: K, value: CustomModelSettings[K]) => {
    edited.current.add(key);
    onChange({ ...draft, custom: { ...custom, [key]: value } });
  };
  const select = (model: DiscoveredModel) => {
    const selections = model.reasoning?.selections ?? model.reasoningSelections;
    const reasoning = selections?.length ? { selections, defaultSelection: null } : null;
    const values: Partial<CustomModelSettings> = {
      name: model.name?.trim() || model.id, contextWindowTokens: model.contextWindowTokens ?? 0,
      maxOutputTokens: model.maxOutputTokens ?? 0, supportsImages: model.supportsImages ?? false,
      supportsToolCall: model.supportsToolCall ?? true, reasoning,
      reasoningControl: reasoning ? "configured" : "provider_default",
    };
    const next = { ...custom };
    for (const key of Object.keys(values) as CustomField[]) {
      if (draft.modelId !== model.id) edited.current.delete(key);
      if (!edited.current.has(key)) Object.assign(next, { [key]: values[key] });
    }
    onChange({ ...draft, modelId: model.id, custom: next });
    const missing = [!model.contextWindowTokens && "上下文窗口", !model.maxOutputTokens && "最大输出"].filter(Boolean);
    setSource(missing.length ? `接口未提供${missing.join("、")}，未填写将使用默认值。` : undefined);
  };
  const test = async () => {
    const version = ++requestVersion.current;
    const error = customModelAddressError(custom.baseUrl);
    if (error) { setResult({ state: "error", message: error, models: [] }); return; }
    setTesting(true); setResult(undefined);
    try {
      const response = await window.eidosRuntime.discoverCustomModels({
        provider: "custom", baseUrl: custom.baseUrl,
        ...(draft.originalId ? { configId: draft.originalId } : {}),
        ...(!draft.originalId || draft.apiKeyEdited || draft.apiKey || custom.baseUrl !== draft.originalBaseUrl ? { apiKey: draft.apiKey.trim() } : {}),
      });
      if (version === requestVersion.current) setResult(response);
    } catch {
      if (version === requestVersion.current) setResult({ state: "error", message: "连接测试失败，请检查接口地址和 API Key。", models: [] });
    } finally { if (version === requestVersion.current) setTesting(false); }
  };
  const inlineStatus = (testing && showProgress) || (!testing && result?.state === "success" && Boolean(result.models?.length));
  return <>
    <div className="custom-model-grid custom-model-provider-row">
      <label><span>提供商 ID <small className="custom-model-required" aria-hidden="true">*</small></span><input aria-label="提供商 ID" aria-required="true" maxLength={64} value={custom.providerId ?? ""} disabled={busy}
        onChange={event => {
          const providerId = event.target.value;
          const followId = !edited.current.has("providerName") && (!custom.providerName || custom.providerName === custom.providerId);
          onChange({ ...draft, custom: { ...custom, providerId, ...(followId ? { providerName: providerId } : {}) } });
        }} placeholder="my-provider" autoCapitalize="none" spellCheck={false} /></label>
      <label><span>提供商名称 <small className="custom-model-required" aria-hidden="true">*</small></span><input aria-label="提供商名称" aria-required="true" maxLength={64} value={custom.providerName} disabled={busy}
        onChange={event => update("providerName", event.target.value)} placeholder="my-provider" /></label>
    </div>
    <div className="custom-model-grid custom-model-endpoint-row">
    <label><span>接口地址 <small className="custom-model-required" aria-hidden="true">*</small></span><input aria-label="接口地址" aria-required="true" type="url" maxLength={2048}
      value={custom.baseUrl} disabled={busy} placeholder="https://api.example.com/v1"
      onChange={event => update("baseUrl", event.target.value)}
      onBlur={event => {
        const raw = event.currentTarget.value.trim().replace(/\/$/, "");
        if (raw.endsWith("/chat/completions")) onChange({ ...draft, custom: { ...custom, baseUrl: raw.slice(0, -17), wireApi: "chat_completions" } });
        else if (raw.endsWith("/responses")) onChange({ ...draft, custom: { ...custom, baseUrl: raw.slice(0, -10), wireApi: "openai_responses", reasoningFormat: "openai" } });
        else if (raw !== custom.baseUrl) onChange({ ...draft, custom: { ...custom, baseUrl: raw } });
      }} /></label>
    <label><span>接口协议</span><select aria-label="接口协议" value={custom.wireApi ?? "chat_completions"} disabled={busy}
      onChange={event => onChange({ ...draft, custom: { ...custom, wireApi: event.target.value as NonNullable<CustomModelSettings["wireApi"]>, reasoningFormat: "openai", reasoning: null, reasoningControl: "provider_default" } })}>
      <option value="chat_completions">Chat Completions</option><option value="openai_responses">Responses</option>
    </select></label>
    </div>
    <div className="custom-model-key-row">
    <label><span>API Key</span><div className="custom-model-secret">
      <div className="custom-model-secret-input">
      <input aria-label="API Key" type={showKey ? "text" : "password"} autoComplete="off" spellCheck={false} maxLength={512}
        placeholder={draft.originalId && !draft.apiKeyEdited ? "留空表示保持原值" : ""} value={draft.apiKey} disabled={busy}
        onChange={event => { setShowKey(false); onChange({ ...draft, apiKey: event.target.value, apiKeyEdited: true }); }} />
      <Tooltip content={showKey ? "隐藏 API Key" : "显示 API Key"}>
        <Button type="button" variant="ghost" size="small" className="custom-model-secret-toggle" aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}
          title={showKey ? "隐藏 API Key" : "显示 API Key"} aria-pressed={showKey} disabled={busy || !draft.apiKey} onClick={() => setShowKey(!showKey)}>{showKey ? <EyeOffIcon /> : <EyeIcon />}</Button>
      </Tooltip>
      </div>
    </div></label>
      <div className="custom-model-test-actions">
      {inlineStatus && <div role="status" aria-live="polite" className={`custom-model-status${!testing ? " is-success" : ""}`}>
        {testing ? "正在获取模型…" : result?.message}{result?.truncated && "（前 500 项）"}
      </div>}
      <Button type="button" variant="secondary" aria-busy={testing || undefined} disabled={busy || testing || !custom.baseUrl.trim()} onClick={() => void test()}>测试连接</Button>
      </div>
    </div>
    {result && !inlineStatus && <div role="status" aria-live="polite" className="custom-model-status">
      {result.message}
      {result?.truncated && " 仅显示前 500 个模型。"}
    </div>}
    <div className="custom-model-section-heading"><strong>模型设置</strong></div>
    <ModelIdCombobox value={draft.modelId} models={result?.models ?? []} disabled={busy}
      onChange={modelId => onChange({ ...draft, modelId })} onSelect={select} />
    {source && <p className="custom-model-hint">{source}</p>}
    <label><span>展示名 <small className="custom-model-required" aria-hidden="true">*</small></span><input aria-label="展示名" aria-required="true" value={custom.name} maxLength={120} disabled={busy}
      onChange={event => update("name", event.target.value)} placeholder="模型在 Eidos 中显示的名称" /></label>
    <div className="custom-model-grid">
      <TokenLimitField label="上下文窗口" value={custom.contextWindowTokens} presets={[262144, 524288, 1048576]} placeholder="默认 128K" disabled={busy} onChange={value => update("contextWindowTokens", value)} />
      <TokenLimitField label="最大输出" value={custom.maxOutputTokens} presets={[32768, 65536, 131072]} placeholder="默认 8K" disabled={busy} onChange={value => update("maxOutputTokens", value)} />
    </div>
    <div className="custom-model-capabilities"><span>输入类型</span><div>
      <label className="custom-model-check"><input type="checkbox" checked disabled />文本</label>
      <label className="custom-model-check"><input type="checkbox" checked={custom.supportsImages ?? false} disabled={busy} onChange={event => update("supportsImages", event.target.checked)} />图像</label>
      <label className="custom-model-check"><input type="checkbox" checked={custom.supportsToolCall ?? true} disabled={busy} onChange={event => update("supportsToolCall", event.target.checked)} />支持工具调用</label>
    </div></div>
    <CustomReasoningFields custom={custom} busy={busy} onChange={next => { edited.current.add("reasoning"); edited.current.add("reasoningFormat"); edited.current.add("reasoningControl"); onChange({ ...draft, custom: next }); }} />
  </>;
}
