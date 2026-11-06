import { useEffect, useRef, useState } from "react";
import type { QoderConnectionState, QoderCatalogEntry } from "../../../../shared/qoder.js";
import { Button } from "../Button.js";
import { Tooltip } from "../Tooltip.js";
import { ToastContainer, type ToastItem } from "../ToastContainer.js";
import { StatusBadge, type StatusTone } from "./StatusBadge.js";
import { TokenLimitField } from "./TokenLimitField.js";
import { ModelInputTypes } from "./ModelInputTypes.js";
import type { ModelDraft } from "./ModelDialog.js";

const messages: Record<string, string> = {
  authentication_failed: "Qoder 尚未登录或登录已失效，请通过浏览器登录。",
  resource_not_ready: "请先准备 Qoder 模型资源。",
  provider_timeout: "Qoder 暂时没有响应，请稍后重试。",
  qoder_connection_failed: "Qoder 连接失败，请重新登录或刷新目录。",
};

// The SDK uses decimal limits; local fallback presets can be multiples of 1024.
const formatTokenPreset = (tokens: number) => `${Math.round(tokens / (tokens % 1024 === 0 ? 1024 : 1000))}K`;

export function QoderModelFields({ draft, busy, onChange, onCatalogUpdated }: {
  draft: ModelDraft; busy: boolean; onChange: (value: ModelDraft) => void;
  onCatalogUpdated?: (() => Promise<void>) | undefined;
}) {
  const [state, setState] = useState<QoderConnectionState>();
  const [error, setError] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [checkingLogin, setCheckingLogin] = useState(false);
  const [acted, setActed] = useState(false);
  const [toast, setToast] = useState<ToastItem | null>(null);
  const mounted = useRef(true);
  const draftRef = useRef(draft);
  const onChangeRef = useRef(onChange);
  const onCatalogUpdatedRef = useRef(onCatalogUpdated);
  const lastCatalogNames = useRef("");
  draftRef.current = draft;
  onChangeRef.current = onChange;
  onCatalogUpdatedRef.current = onCatalogUpdated;

  const selectModel = (model: QoderCatalogEntry, current: ModelDraft = draftRef.current) => {
    onChangeRef.current({ ...current, modelId: model.id, qoder: {
      accountRef: "default", contextWindowTokens: model.defaultContextWindow ?? 128000,
      maxOutputTokens: model.maxOutputTokens ?? 8192,
      contextWindowLimit: model.contextWindows?.length ? Math.max(...model.contextWindows) : null,
      outputTokenLimit: model.maxOutputTokens ?? null,
      supportsImages: model.supportsImages ?? false, supportsToolCall: true, reasoning: model.reasoning ?? null,
    } });
  };
  const apply = (next: QoderConnectionState) => {
    if (!mounted.current) return;
    setState(next);
    if (next.phase === "ready") {
      const names = JSON.stringify(next.models?.map(model => [model.id, model.name]) ?? []);
      if (names !== lastCatalogNames.current) {
        lastCatalogNames.current = names;
        void onCatalogUpdatedRef.current?.().catch(() => {
          if (mounted.current) setError("模型名称已更新，但列表读取失败，请重新打开设置。");
        });
      }
    }
    if (next.phase === "ready" && !draftRef.current.modelId) {
      const first = next.models?.find(model => model.enabled && model.id === "efficient") ?? next.models?.find(model => model.enabled);
      if (first) selectModel(first);
    }
  };

  useEffect(() => {
    mounted.current = true;
    let active = true;
    let checkedReadyState = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        let next = await window.eidosRuntime.qoderConnection({ action: "status" });
        if (!active) return;
        if (!next.resourceReady) checkedReadyState = false;
        else if (!checkedReadyState) {
          checkedReadyState = true;
          if (!["checking", "logging_in", "refreshing"].includes(next.phase ?? "idle")) {
            setCheckingLogin(true);
            next = await window.eidosRuntime.qoderConnection({ action: "check" });
            if (!active) return;
          }
        }
        apply(next);
      }
      catch { if (active) setError("Qoder 状态读取失败。"); }
      finally { if (active) setCheckingLogin(false); }
      if (active) timer = setTimeout(() => { void poll(); }, 1500);
    };
    void poll();
    return () => { active = false; mounted.current = false; clearTimeout(timer); };
  }, []);

  const checking = checkingLogin || state?.phase === "checking";
  const loggedIn = state?.resourceReady && state?.phase === "ready";
  const waiting = requesting || checking || state?.resourcePreparing || state?.phase === "logging_in" || state?.phase === "refreshing";
  async function act(action: "login" | "refresh" | "prepare") {
    setActed(true);
    setRequesting(true);
    setError("");
    try {
      if (action === "prepare") {
        const result = await window.eidosRuntime.retryResource({ packId: "qoder" });
        if (!result.accepted) setError("当前构建没有 Qoder 资源索引，请重新构建 Runtime。");
      } else apply(await window.eidosRuntime.qoderConnection({ action }));
    } catch { if (mounted.current) setError("Qoder 操作失败，请稍后重试。"); }
    finally { if (mounted.current) setRequesting(false); }
  }
  const options = state?.models?.filter(model => model.enabled) ?? [];
  const selectedModel = options.find(model => model.id === draft.modelId);

  let statusTone: StatusTone = "neutral";
  let statusLabel = "检测中…";
  if (state) {
    if (!state.resourceReady) {
      if (state.resourcePreparing) {
        statusTone = "info";
        statusLabel = "准备中…";
      } else if (state.resourceError) {
        statusTone = "danger";
        statusLabel = "资源异常";
      } else {
        statusTone = "warning";
        statusLabel = "未准备";
      }
    } else if (checking) {
      statusTone = "info";
      statusLabel = "正在检查登录…";
    } else if (state.phase === "logging_in") {
      statusTone = "info";
      statusLabel = "正在登录…";
    } else if (state.phase === "refreshing") {
      statusTone = "info";
      statusLabel = "正在刷新…";
    } else if (state.phase === "ready") {
      statusTone = options.length > 0 ? "success" : "neutral";
      statusLabel = options.length > 0 ? "已登录" : "无可用模型";
    } else if (state.errorCode === "authentication_failed") {
      statusTone = "danger";
      statusLabel = "登录已失效";
    } else if (state.errorCode) {
      statusTone = "danger";
      statusLabel = "连接异常";
    } else {
      statusTone = "neutral";
      statusLabel = "未登录";
    }
  }
  if (checkingLogin && !state) { statusTone = "info"; statusLabel = "正在检查登录…"; }

  const statusMessage = state?.resourcePreparing
    ? "正在准备 Qoder 模型资源…"
    : checking
    ? "正在检查本地登录状态…"
    : state?.phase === "logging_in"
    ? "请在系统浏览器中完成 Qoder 登录。"
    : state?.phase === "refreshing"
    ? "正在读取模型目录…"
    : state?.phase === "ready" && acted
    ? "模型目录已更新。"
    : "";

  const isSuccessStatus = state?.phase === "ready" && !waiting;
  const errorMessage =
    error ||
    (state?.resourceError ? "Qoder 资源准备失败，请重试。" : "") ||
    (state?.errorCode ? messages[state.errorCode] || "Qoder 操作失败，请重新登录或刷新目录。" : "");

  useEffect(() => {
    if (errorMessage) {
      setToast({
        id: `qoder-error-${Date.now()}-${errorMessage}`,
        message: errorMessage,
        type: "error",
        duration: 4500,
      });
    } else {
      setToast(null);
    }
  }, [errorMessage]);

  const updateTokens = (field: "contextWindowTokens" | "maxOutputTokens", value: number) => {
    onChange({
      ...draft,
      qoder: {
        ...draft.qoder,
        accountRef: draft.qoder?.accountRef ?? "default",
        contextWindowTokens: field === "contextWindowTokens" ? value : (draft.qoder?.contextWindowTokens ?? 128000),
        maxOutputTokens: field === "maxOutputTokens" ? value : (draft.qoder?.maxOutputTokens ?? 8192),
      },
    });
  };

  return (
    <>
      {toast && (
        <ToastContainer
          toasts={[toast]}
          onDismiss={() => {
            setToast(null);
            setError("");
          }}
        />
      )}

      <div className="qoder-model-section">
        <div className="qoder-model-field-header">
          <span className="qoder-model-field-label">账号授权</span>
          {statusMessage && (
            <div
              role="status"
              aria-live="polite"
              className={`qoder-model-status${isSuccessStatus ? " is-success" : ""}`}
            >
              {statusMessage}
            </div>
          )}
        </div>
        <div className="qoder-model-connection-bar" aria-busy={waiting}>
          <div className="qoder-model-identity">
            <StatusBadge tone={statusTone}>{statusLabel}</StatusBadge>
          </div>
          <div className="qoder-model-actions">
            {!state?.resourceReady && (
              <Tooltip content="准备 Qoder 本地依赖与模型资源">
                <Button
                  type="button"
                  variant="secondary"
                  size="small"
                  loading={Boolean(state?.resourcePreparing)}
                  disabled={busy || waiting}
                  onClick={() => void act("prepare")}
                >
                  准备模型资源
                </Button>
              </Tooltip>
            )}
            <Tooltip content={loggedIn ? "通过系统浏览器重新登录或切换 Qoder 账号" : "通过系统浏览器登录 Qoder 账号"}>
              <Button
                type="button"
                variant="secondary"
                size="small"
                loading={state?.phase === "logging_in"}
                disabled={busy || waiting || !state?.resourceReady}
                onClick={() => void act("login")}
              >
                {loggedIn ? "重新登录" : "浏览器登录"}
              </Button>
            </Tooltip>
            <Tooltip content="重新拉取 Qoder 账号支持的模型列表">
              <Button
                type="button"
                variant="secondary"
                size="small"
                loading={state?.phase === "refreshing"}
                disabled={busy || waiting || !state?.resourceReady}
                onClick={() => void act("refresh")}
              >
                刷新模型目录
              </Button>
            </Tooltip>
          </div>
        </div>
      </div>

      <div className="custom-model-section-heading">
        <strong>模型设置</strong>
      </div>

      <label>
        <span>模型名称</span>
        <div className="model-select-control">
          <select
            aria-label="模型名称"
            value={draft.modelId}
            disabled={busy || waiting || !options.length}
            onChange={event => { const model = options.find(m => m.id === event.target.value); if (model) selectModel(model, draft); }}
          >
            {!options.some(model => model.id === draft.modelId) && (
              <option value={draft.modelId}>{draft.modelId || "请先刷新模型目录"}</option>
            )}
            {options.map(model => (
              <option key={model.id} value={model.id}>{model.name}</option>
            ))}
          </select>
          <span className="model-select-control__chevron" aria-hidden="true">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </span>
        </div>
      </label>

      <label><span>思考强度</span><div className="model-select-control"><select aria-label="思考强度" disabled={busy || !draft.qoder?.reasoning?.selections.length}
        value={draft.qoder?.reasoning?.defaultSelection ?? ""}
        onChange={event => { if (draft.qoder?.reasoning) onChange({ ...draft, qoder: { ...draft.qoder, reasoning: { ...draft.qoder.reasoning, defaultSelection: event.target.value ? event.target.value as NonNullable<typeof draft.qoder.reasoning.defaultSelection> : null } } }); }}>
        <option value="">模型默认</option>
        {draft.qoder?.reasoning?.selections.map(effort => <option key={effort} value={effort}>{{ none: "关闭", thinking: "开启", low: "低", medium: "中", high: "高", xhigh: "超高", max: "最高" }[effort]}</option>)}
      </select></div></label>
      <ModelInputTypes supportsImages={draft.qoder?.supportsImages ?? false} supportsToolCall={draft.qoder?.supportsToolCall ?? true} disabled={busy}
        onImagesChange={value => { if (draft.qoder) onChange({ ...draft, qoder: { ...draft.qoder, supportsImages: value } }); }}
        onToolCallChange={value => { if (draft.qoder) onChange({ ...draft, qoder: { ...draft.qoder, supportsToolCall: value } }); }} />
      <div className="custom-model-grid">
        <TokenLimitField
          label="上下文窗口"
          value={draft.qoder?.contextWindowTokens ?? 128000}
          presets={selectedModel?.contextWindows?.length ? selectedModel.contextWindows : [32768, 65536, 131072]}
          formatPreset={formatTokenPreset}
          placeholder="默认 128K"
          disabled={busy}
          onChange={value => updateTokens("contextWindowTokens", value)}
        />
        <TokenLimitField
          label="最大输出"
          value={draft.qoder?.maxOutputTokens ?? 8192}
          presets={selectedModel?.maxOutputTokens ? [8192, 16384, selectedModel.maxOutputTokens].filter((v, i, a) => v <= selectedModel.maxOutputTokens! && a.indexOf(v) === i) : [8192, 16384, 32768]}
          formatPreset={formatTokenPreset}
          placeholder="默认 8K"
          disabled={busy}
          onChange={value => updateTokens("maxOutputTokens", value)}
        />
      </div>
    </>
  );
}
