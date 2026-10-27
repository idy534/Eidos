import { useEffect, useRef, useState } from "react";
import type {
  Credentialchanges,
  DeepSeekSearchOptions,
  DoubaoSearchOptions,
  LangSearchOptions,
  Provider,
  WebSearchConfig,
  WebSearchOfficialPageRequest,
  WebSearchResponse,
  WebSearchSaveRequest,
} from "../../../../shared/web-search.generated.js";
import { Button } from "../Button.js";
import { StatusBadge } from "../StatusBadge.js";
import { ToastContainer } from "../ToastContainer.js";
import { WebSearchProviderFields } from "./WebSearchProviderFields.js";

type KeyDraft = { value: string; visible: boolean };
const emptyKeys = (): Record<Provider, KeyDraft> => ({
  deepseek: { value: "", visible: false },
  langsearch: { value: "", visible: false },
  doubao: { value: "", visible: false },
});
const keysFromResponse = (value: WebSearchResponse): Record<Provider, KeyDraft> => ({
  deepseek: { value: value.credentialValues?.deepseek ?? "", visible: false },
  langsearch: { value: value.credentialValues?.langsearch ?? "", visible: false },
  doubao: { value: value.credentialValues?.doubao ?? "", visible: false },
});
const providers = ["deepseek", "langsearch", "doubao"] as const;
const names = { deepseek: "DeepSeek Search", langsearch: "LangSearch", doubao: "豆包搜索" };

export function WebSearchSettings({
  onShowToast,
}: {
  onShowToast?: (message: string, type: "success" | "info" | "error") => void;
}) {
  const [saved, setSaved] = useState<WebSearchResponse>();
  const [draft, setDraft] = useState<WebSearchConfig>();
  const [keys, setKeys] = useState(emptyKeys);
  const [expanded, setExpanded] = useState<Partial<Record<Provider, boolean>>>({});
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const keysRef = useRef(keys);
  const savedRef = useRef<WebSearchResponse | null>(null);
  const draftRef = useRef<WebSearchConfig | null>(null);
  const busyRef = useRef(false);
  const queuedChangesRef = useRef<Credentialchanges>([]);
  const pendingConfigRef = useRef<WebSearchConfig | null>(null);
  const keyDebounceTimers = useRef<Partial<Record<Provider, ReturnType<typeof setTimeout>>>>({});
  const optionsDebounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const errorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function triggerError(message: string) {
    if (onShowToast) {
      onShowToast(message, "error");
    } else {
      setError(message);
      if (errorTimerRef.current) {
        clearTimeout(errorTimerRef.current);
      }
      errorTimerRef.current = setTimeout(() => {
        setError("");
        errorTimerRef.current = null;
      }, 3500);
    }
  }

  useEffect(() => {
    let active = true;
    window.eidosRuntime
      .readWebSearch()
      .then((value) => {
        if (active) {
          savedRef.current = value;
          draftRef.current = value.config;
          setSaved(value);
          setDraft(value.config);
          keysRef.current = keysFromResponse(value);
          setKeys(keysRef.current);
        }
      })
      .catch(() => {
        if (active) triggerError("网页搜索配置无法读取。请重试。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      for (const timer of Object.values(keyDebounceTimers.current)) {
        if (timer) clearTimeout(timer);
      }
      if (optionsDebounceTimer.current) clearTimeout(optionsDebounceTimer.current);
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    };
  }, []);

  async function executeSave(
    nextConfig: WebSearchConfig,
    credentialChanges: Credentialchanges = [],
  ): Promise<WebSearchResponse | undefined> {
    if (!savedRef.current) return;

    if (busyRef.current) {
      pendingConfigRef.current = nextConfig;
      if (credentialChanges.length > 0) {
        queuedChangesRef.current = [...queuedChangesRef.current, ...credentialChanges];
      }
      return;
    }

    busyRef.current = true;
    setBusy(true);

    try {
      const allChanges = [...new Map(
        [...queuedChangesRef.current, ...credentialChanges].map((change) => [change.provider, change]),
      ).values()];
      queuedChangesRef.current = [];
      const req: WebSearchSaveRequest = {
        expectedRevision: savedRef.current.revision,
        config: nextConfig,
        ...(allChanges.length > 0 ? { credentialChanges: allChanges } : {}),
      };
      const res = await window.eidosRuntime.saveWebSearch(req);
      savedRef.current = res;
      setSaved(res);
      if (draftRef.current === nextConfig) {
        draftRef.current = res.config;
        setDraft(res.config);
      }
      onShowToast?.("设置已保存", "success");
      return res;
    } catch {
      triggerError("网页搜索设置保存失败。你可以刷新配置后重试。页面保留了你的编辑。");
    } finally {
      busyRef.current = false;
      setBusy(false);
      if (pendingConfigRef.current) {
        const next = pendingConfigRef.current;
        pendingConfigRef.current = null;
        void executeSave(next, []);
      }
    }
  }

  async function refresh() {
    setBusy(true);
    try {
      const value = await window.eidosRuntime.readWebSearch();
      savedRef.current = value;
      setSaved(value);
      draftRef.current = value.config;
      setDraft(value.config);
      keysRef.current = keysFromResponse(value);
      setKeys(keysRef.current);
      onShowToast?.("配置已刷新", "info");
    } catch {
      triggerError("网页搜索配置无法读取。请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function open(provider: Provider, target: WebSearchOfficialPageRequest["target"]) {
    try {
      await window.eidosRuntime.openWebSearchOfficialPage({ provider, target });
    } catch {
      triggerError("官方页面无法打开。请检查系统浏览器后重试。");
    }
  }

  const setKey = (provider: Provider, change: Partial<KeyDraft>) => {
    keysRef.current = { ...keysRef.current, [provider]: { ...keysRef.current[provider], ...change } };
    setKeys(keysRef.current);
  };

  async function commitKeyInput(provider: Provider) {
    if (keyDebounceTimers.current[provider]) {
      clearTimeout(keyDebounceTimers.current[provider]);
      delete keyDebounceTimers.current[provider];
    }
    const rawValue = keysRef.current[provider].value.trim();
    if (!draftRef.current) return;
    if (!rawValue) {
      if (savedRef.current?.credentials[provider] !== "missing" || busyRef.current) await clearKey(provider);
      return;
    }
    if (!busyRef.current && rawValue === savedRef.current?.credentialValues?.[provider]) return;
    await executeSave(draftRef.current, [{ provider, action: "set", value: rawValue }]);
  }

  async function clearKey(provider: Provider) {
    if (!savedRef.current || !draftRef.current) return;
    if (keyDebounceTimers.current[provider]) {
      clearTimeout(keyDebounceTimers.current[provider]);
      delete keyDebounceTimers.current[provider];
    }
    const nextConfig = draftRef.current.defaultProvider === provider
      ? { ...draftRef.current, defaultProvider: null }
      : draftRef.current;
    setKey(provider, { value: "" });
    setDraft(nextConfig);
    draftRef.current = nextConfig;
    await executeSave(nextConfig, [{ provider, action: "clear" }]);
  }

  function handleKeyChange(provider: Provider, value: string) {
    setKey(provider, { value });
    if (keyDebounceTimers.current[provider]) {
      clearTimeout(keyDebounceTimers.current[provider]);
    }
    keyDebounceTimers.current[provider] = setTimeout(() => {
      void commitKeyInput(provider);
    }, 1200);
  }

  async function handleToggleDefault(provider: Provider, checked: boolean) {
    if (!savedRef.current || !draftRef.current) return;
    const currentDraft = draftRef.current;
    const keyVal = keysRef.current[provider].value.trim();

    if (checked) {
      const isAvailable = Boolean(keyVal);
      if (!isAvailable) {
        triggerError("请先为默认搜索服务填写 API Key。");
        return;
      }
      const changes: Credentialchanges = [];
      if (keyVal) {
        changes.push({ provider, action: "set", value: keyVal });
      }
      const nextConfig: WebSearchConfig = { ...currentDraft, defaultProvider: provider };
      setDraft(nextConfig);
      draftRef.current = nextConfig;
      await executeSave(nextConfig, changes);
    } else {
      const nextConfig: WebSearchConfig = { ...currentDraft, defaultProvider: null };
      setDraft(nextConfig);
      draftRef.current = nextConfig;
      await executeSave(nextConfig);
    }
  }

  function handleOptionsChange(
    provider: Provider,
    options: DeepSeekSearchOptions | LangSearchOptions | DoubaoSearchOptions,
  ) {
    if (!draftRef.current) return;
    const nextConfig: WebSearchConfig = { ...draftRef.current, [provider]: options };
    setDraft(nextConfig);
    draftRef.current = nextConfig;

    if (optionsDebounceTimer.current) {
      clearTimeout(optionsDebounceTimer.current);
    }
    optionsDebounceTimer.current = setTimeout(() => {
      void executeSave(draftRef.current ?? nextConfig);
    }, 400);
  }

  return (
    <div className="web-search-settings">
      {/* 独立环境下的全局悬浮条兜底（3.5 秒自动关闭） */}
      {error && (
        <ToastContainer
          toasts={[{ id: "web-search-error", message: error, type: "error", duration: 3500 }]}
          onDismiss={() => setError("")}
        />
      )}

      <header className="web-search-header">
        <div className="web-search-header-info">
          <h1>网页搜索</h1>
          <p className="web-search-subtitle">
            配置联网检索提供商。开启默认服务后，智能体可在任务中获取最新网络信息。
          </p>
        </div>
        {!loading && (
          <Button
            size="small"
            disabled={busy}
            onClick={() => void refresh()}
            className="web-search-refresh-btn"
          >
            刷新配置
          </Button>
        )}
      </header>

      {loading && (
        <div className="web-search-notice-banner web-search-notice-banner--loading" role="status">
          <span className="web-search-loading-dot" />
          <span>Eidos 正在读取搜索配置…</span>
        </div>
      )}

      {draft && saved && (
        <div className="web-search-form">
          <div className="web-search-provider-list">
            {providers.map((provider) => {
              const key = keys[provider];
              const status = saved.credentials[provider] ?? "missing";
              const selected = draft.defaultProvider === provider;
              const available = Boolean(key.value.trim());

              return (
                <section
                  key={provider}
                  className={`web-search-card ${selected ? "web-search-card--selected" : ""}`}
                >
                  <div className="web-search-card-header">
                    <div className="web-search-provider-identity">
                      <div className={`web-search-provider-icon web-search-provider-icon--${provider}`}>
                        {provider === "deepseek" ? (
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="11" cy="11" r="8" />
                            <path d="m21 21-4.3-4.3" />
                          </svg>
                        ) : (
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="12" cy="12" r="10" />
                            <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
                            <path d="M2 12h20" />
                          </svg>
                        )}
                      </div>
                      <div className="web-search-provider-titles">
                        <div className="web-search-provider-name-row">
                          <h2>{names[provider]}</h2>
                          <StatusBadge
                            tone={selected && available ? "success" : status === "unavailable" ? "warning" : "neutral"}
                          >
                            {selected && available
                              ? "默认使用"
                              : status === "stored"
                              ? "Key 已保存"
                              : status === "unavailable"
                              ? "Key 文件不可用"
                              : "尚未配置 Key"}
                          </StatusBadge>
                        </div>
                        <p className="web-search-provider-desc">
                          {provider === "deepseek"
                            ? "DeepSeek 官方搜索 API"
                            : provider === "doubao"
                            ? "火山引擎豆包搜索 Custom 版 · 查询最多 100 字符"
                            : "LangSearch 官方网页搜索 API"}
                        </p>
                        {provider === "doubao" && (
                          <p className="web-search-provider-desc">
                            用户可填写搜索专用 Key，或已开启豆包搜索抵扣的 Agent Plan 专属 Key。计费方式由 Key 决定。
                          </p>
                        )}
                      </div>
                    </div>

                    <div className="web-search-card-actions">
                      <label
                        className={`web-search-default-toggle ${
                          selected ? "web-search-default-toggle--checked" : ""
                        }`}
                      >
                        <input
                          type="checkbox"
                          aria-label={`${names[provider]} 默认使用`}
                          checked={selected}
                          onChange={(event) =>
                            void handleToggleDefault(provider, event.target.checked)
                          }
                        />
                        <span className="web-search-toggle-label">
                          {selected ? "默认搜索" : "设为默认"}
                        </span>
                      </label>
                    </div>
                  </div>

                  <div className="web-search-card-body">
                    <div className="web-search-key-control">
                      <div className="web-search-key-header">
                        <label className="web-search-key-label" htmlFor={`key-input-${provider}`}>
                          API 密钥
                        </label>
                        <div className="web-search-quick-links">
                          <Button
                            size="small"
                            variant="ghost"
                            className="web-search-link-btn"
                            onClick={() => void open(provider, "keys")}
                          >
                            获取 API Key ↗
                          </Button>
                          <Button
                            size="small"
                            variant="ghost"
                            className="web-search-link-btn"
                            onClick={() => void open(provider, "docs")}
                          >
                            官方文档 ↗
                          </Button>
                          <Button
                            size="small"
                            variant="ghost"
                            className="web-search-link-btn"
                            onClick={() => void open(provider, "pricing")}
                          >
                            费用与额度 ↗
                          </Button>
                        </div>
                      </div>

                      <div className="web-search-key-input-row">
                        <div className="web-search-input-field">
                          <input
                            id={`key-input-${provider}`}
                            aria-label={`${names[provider]} API Key`}
                            autoComplete="new-password"
                            spellCheck={false}
                            type={key.visible ? "text" : "password"}
                            maxLength={1024}
                            value={key.value}
                            placeholder={status === "unavailable" ? "文件不可用，输入新 Key" : "输入 API Key"}
                            onChange={(event) => handleKeyChange(provider, event.target.value)}
                            onBlur={() => void commitKeyInput(provider)}
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                void commitKeyInput(provider);
                              }
                            }}
                          />
                          <button
                            type="button"
                            className="web-search-eye-btn"
                            aria-pressed={key.visible}
                            aria-label={`${key.visible ? "隐藏" : "显示"} ${names[provider]} API Key`}
                            disabled={!key.value}
                            onClick={() => setKey(provider, { visible: !key.visible })}
                          >
                            {key.visible ? (
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
                                <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
                                <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
                                <line x1="2" x2="22" y1="2" y2="22" />
                              </svg>
                            ) : (
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
                                <circle cx="12" cy="12" r="3" />
                              </svg>
                            )}
                          </button>
                        </div>

                        <button
                          type="button"
                          className="web-search-expand-btn"
                          aria-expanded={expanded[provider] ?? false}
                          onClick={() => setExpanded((old) => ({ ...old, [provider]: !old[provider] }))}
                        >
                          <span className="web-search-expand-title">
                            <svg
                              className={`web-search-chevron ${
                                expanded[provider] ? "web-search-chevron--open" : ""
                              }`}
                              width="12"
                              height="12"
                              viewBox="0 0 12 12"
                              fill="none"
                            >
                              <path
                                d="M2.5 4.5L6 8L9.5 4.5"
                                stroke="currentColor"
                                strokeWidth="1.5"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              />
                            </svg>
                            其他设置
                          </span>
                        </button>
                      </div>
                    </div>
                  </div>

                  {expanded[provider] && (
                    <div className="web-search-expand-content">
                      <WebSearchProviderFields
                        provider={provider}
                        options={draft[provider] ?? {}}
                        onChange={(options) => handleOptionsChange(provider, options)}
                      />
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
