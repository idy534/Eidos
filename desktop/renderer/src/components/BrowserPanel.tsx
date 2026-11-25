import { useEffect, useRef, useState } from "react";
import { parse as parseDomain } from "tldts";
import type { BrowserAnnotation, BrowserPageState } from "../contracts.js";
import { userFacingError } from "../session-state.js";
import { useInputContext } from "./InputContext.js";
import { Tooltip } from "./Tooltip.js";

function isIpv4(hostname: string): boolean {
  const parts = hostname.split(".");
  return parts.length === 4 && parts.every((part) => /^\d+$/.test(part) && Number(part) <= 255);
}

function isLocalHost(hostname: string): boolean {
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (host === "localhost" || host.endsWith(".localhost") || host === "::1" || host === "0.0.0.0") return true;
  if (!isIpv4(host)) return false;
  const numbers = host.split(".").map(Number);
  const first = numbers[0] ?? -1;
  const second = numbers[1] ?? -1;
  return first === 127 || first === 10 || (first === 172 && second >= 16 && second <= 31) || (first === 192 && second === 168);
}

export function resolveBrowserTarget(input: string): string | undefined {
  const value = input.trim();
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    if (["http:", "https:", "eidos-preview:"].includes(parsed.protocol)) return value;
  } catch {
    // Protocol-less addresses are checked below.
  }
  if (!/\s/.test(value)) {
    try {
      const candidate = new URL(`https://${value}`);
      const host = candidate.hostname;
      const domain = parseDomain(value, { allowPrivateDomains: true });
      const local = isLocalHost(host);
      if (!candidate.username && !candidate.password && (domain.isIp || Boolean(domain.domain) || local)) {
        return `${local ? "http" : "https"}://${value}`;
      }
    } catch {
      // Non-address input is sent to Google search.
    }
  }
  return `https://www.google.com/search?q=${encodeURIComponent(value)}`;
}

export function getBrowserTabTitle(url: string): string {
  const target = url.trim();
  if (!target) return "新标签页";
  try {
    const parsed = new URL(
      target.startsWith("http://") || target.startsWith("https://") || target.startsWith("eidos-preview://")
        ? target
        : `https://${target}`,
    );
    if (parsed.protocol === "eidos-preview:") {
      const parts = parsed.pathname.split("/").filter(Boolean);
      return parts.at(-1) || "文件预览";
    }
    return parsed.hostname || target;
  } catch {
    return target;
  }
}

function formatBrowserAnnotationExcerpt(annotation: BrowserAnnotation): string {
  const lines: string[] = [];
  if (annotation.title.trim()) lines.push(`标题：${annotation.title.trim()}`);
  if (annotation.url) lines.push(`网址：${annotation.url}`);
  if (annotation.selection.trim()) lines.push(`选中内容：${annotation.selection.trim()}`);
  if (annotation.elements.length > 0) {
    lines.push("可见交互与关键元素：");
    for (const element of annotation.elements.slice(0, 25)) {
      const label = element.text ? ` ${element.text}` : "";
      lines.push(`- <${element.tag}>${label} (${Math.round(element.x)}%, ${Math.round(element.y)}%)`);
    }
  }
  const text = lines.join("\n").trim() || annotation.url || "网页标注";
  return text.slice(0, 16_000);
}

export function BrowserPanel({
  browserId,
  sessionId,
  executionKey,
  active,
  request,
  workspaceRoot,
  onPageStateChange,
  onAnnotate,
}: {
  browserId: string;
  sessionId: string;
  executionKey: string;
  active: boolean;
  workspaceRoot?: string;
  request?: { url: string; id: number } | undefined;
  onPageStateChange?: (state: BrowserPageState) => void;
  onAnnotate?: (annotation: BrowserAnnotation) => void;
}) {
  const inputContext = useInputContext();
  const viewport = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const addressDirty = useRef(false);
  const [address, setAddress] = useState("");
  const [page, setPage] = useState<BrowserPageState>({
    url: "",
    title: "",
    loading: false,
    canGoBack: false,
    canGoForward: false,
  });
  const [error, setError] = useState("");
  const [annotating, setAnnotating] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);

  const onPageStateChangeRef = useRef(onPageStateChange);
  onPageStateChangeRef.current = onPageStateChange;

  useEffect(() => {
    generation.current++;
    setPage({ url: "", title: "", loading: false, canGoBack: false, canGoForward: false });
    addressDirty.current = false;
    setAddress("");
    return () => { generation.current++; void window.eidosRuntime.closeBrowser(sessionId, browserId); };
  }, [browserId, sessionId, executionKey]);

  function applyIncomingState(next: BrowserPageState): void {
    setPage(next);
    if (!addressDirty.current && next.url) {
      setAddress(next.url);
    }
    onPageStateChangeRef.current?.(next);
  }

  async function open(url: string) {
    const target = resolveBrowserTarget(url);
    if (!target) return;
    const token = ++generation.current;
    addressDirty.current = false;
    setAddress(target);
    setError("");
    const optimisticTitle = getBrowserTabTitle(target);
    setPage((current) => ({ ...current, url: current.url || target, loading: true }));
    onPageStateChangeRef.current?.({ url: target, title: optimisticTitle, loading: true });
    try {
      const next = await window.eidosRuntime.openBrowser(sessionId, browserId, target, workspaceRoot);
      if (token === generation.current) {
        addressDirty.current = false;
        setPage(next);
        setAddress(next.url || target);
        onPageStateChangeRef.current?.(next);
      }
    } catch (cause) {
      if (token === generation.current) {
        const message = userFacingError(cause);
        setError(message);
        setPage((current) => ({ ...current, loading: false }));
        onPageStateChangeRef.current?.({ url: target, title: optimisticTitle, loading: false, error: message });
      }
    }
  }
  async function goBack(): Promise<void> {
    if (!page.canGoBack) return;
    addressDirty.current = false;
    setError("");
    try {
      const next = await window.eidosRuntime.browserGoBack?.(sessionId, browserId);
      if (next) applyIncomingState(next);
    } catch (cause) {
      setError(userFacingError(cause));
    }
  }
  async function goForward(): Promise<void> {
    if (!page.canGoForward) return;
    addressDirty.current = false;
    setError("");
    try {
      const next = await window.eidosRuntime.browserGoForward?.(sessionId, browserId);
      if (next) applyIncomingState(next);
    } catch (cause) {
      setError(userFacingError(cause));
    }
  }
  async function annotatePage(): Promise<void> {
    if (!page.url || page.loading || annotating) return;
    setAnnotating(true);
    setError("");
    try {
      const annotation = await window.eidosRuntime.annotateBrowser(sessionId, browserId);
      if (onAnnotate) {
        onAnnotate(annotation);
      } else if (inputContext) {
        await inputContext.add({
          kind: "excerpt",
          source: annotation.url || `browser:${browserId}`,
          label: annotation.title?.trim() || getBrowserTabTitle(annotation.url) || "网页标注",
          text: formatBrowserAnnotationExcerpt(annotation),
        });
      }
    } catch (cause) {
      setError(userFacingError(cause));
    } finally {
      setAnnotating(false);
    }
  }
  function refresh(): void {
    setRefreshVersion((version) => version + 1);
    void open(page.url || address);
  }
  function stop(): void {
    generation.current++;
    setPage((current) => ({ ...current, loading: false }));
    onPageStateChangeRef.current?.({ ...page, loading: false });
    void window.eidosRuntime.stopBrowser?.(sessionId, browserId).catch(() => {});
  }
  useEffect(() => { if (request) void open(request.url); }, [request?.id, executionKey]);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const sync = () => {
      const rect = element.getBoundingClientRect();
      void window.eidosRuntime.setBrowserBounds(sessionId, browserId, active && page.url && !document.querySelector('[aria-modal="true"], dialog[open]') ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null).catch(() => {});
    };
    const observer = new ResizeObserver(sync); observer.observe(element);
    const overlays = new MutationObserver(sync); overlays.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-modal", "open"] });
    window.addEventListener("resize", sync); window.addEventListener("scroll", sync, true); sync();
    return () => { overlays.disconnect(); observer.disconnect(); window.removeEventListener("resize", sync); window.removeEventListener("scroll", sync, true); void window.eidosRuntime.setBrowserBounds(sessionId, browserId, null); };
  }, [active, page.url, browserId, sessionId]);

  useEffect(() => {
    const unsubscribe = window.eidosRuntime.onBrowserState?.((event) => {
      if (event.sessionId !== sessionId || event.browserId !== browserId) return;
      applyIncomingState(event.state);
    });
    if (active && page.url) {
      void window.eidosRuntime.readBrowserState?.(sessionId, browserId).then((next) => {
        if (next && (next.url || next.error)) applyIncomingState(next);
      }).catch(() => {});
    }
    return () => {
      unsubscribe?.();
    };
  }, [active, browserId, sessionId]);

  return <section className="browser-panel" aria-label="网页预览">
    <form className="browser-navigation" onSubmit={(event) => { event.preventDefault(); void open(address); }}>
      <div className="browser-navigation__history" aria-label="网页历史导航">
        <Tooltip content="后退">
          <button
            type="button"
            className="browser-navigation__button"
            aria-label="后退"
            disabled={!page.canGoBack}
            onClick={() => void goBack()}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 5 7.5 10l5 5" /></svg>
          </button>
        </Tooltip>
        <Tooltip content="前进">
          <button
            type="button"
            className="browser-navigation__button"
            aria-label="前进"
            disabled={!page.canGoForward}
            onClick={() => void goForward()}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7.5 5 5 5-5 5" /></svg>
          </button>
        </Tooltip>
        <Tooltip content={page.loading ? "停止加载" : "刷新"}>
          <button
            type="button"
            className={`browser-navigation__button browser-navigation__button--refresh${page.loading ? " browser-navigation__button--stop" : ""}`}
            aria-label={page.loading ? "停止加载" : "刷新"}
            disabled={!page.loading && (!page.url && !address.trim())}
            onClick={page.loading ? stop : refresh}
          >
            {page.loading ? (
              <svg
                className={[
                  "browser-navigation__stop-icon",
                  refreshVersion > 0 ? "browser-navigation__refresh-icon--refreshing" : "",
                ].filter(Boolean).join(" ")}
                viewBox="0 0 20 20"
                aria-hidden="true"
              >
                <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            ) : (
              <svg
                key={refreshVersion}
                className={refreshVersion > 0 ? "browser-navigation__refresh-icon--refreshing" : undefined}
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path d="M20.5 10.5a8.5 8.5 0 0 0-15.2-4L3 8" />
                <path d="M3 4.5V8h3.5" />
                <path d="M3.5 13.5a8.5 8.5 0 0 0 15.2 4L21 16" />
                <path d="M21 19.5V16h-3.5" />
              </svg>
            )}
          </button>
        </Tooltip>
      </div>
      <input
        aria-label="网页地址"
        className="browser-navigation__address"
        value={address}
        onChange={(event) => { addressDirty.current = true; setAddress(event.target.value); }}
        placeholder="搜索或输入网址"
      />
      <Tooltip content="标注当前页面并加入对话引用">
        <button
          type="button"
          className="browser-navigation__button"
          aria-label="标注页面"
          disabled={!page.url || page.loading || annotating}
          onClick={() => void annotatePage()}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <rect x="3.5" y="4" width="13" height="10" rx="2" />
            <path d="M7 16.5h6M10 14v2.5M6.5 8.5h4M6.5 11h7" />
          </svg>
        </button>
      </Tooltip>
    </form>
    {page.loading && (
      <div className="browser-progress-bar" role="progressbar" aria-label="网页加载进度">
        <div className="browser-progress-bar__indicator" />
      </div>
    )}
    {(error || page.error) && <p role="alert">{error || page.error}</p>}
    <div ref={viewport} className="browser-viewport">
      {page.loading && (
        <div className="browser-loading" role="status" aria-live="polite">
          <div className="browser-loading__icon-wrapper" aria-hidden="true">
            <svg viewBox="0 0 32 32" className="browser-loading__icon">
              <circle cx="16" cy="16" r="11.5" />
              <ellipse cx="16" cy="16" rx="4.5" ry="11.5" />
              <path d="M4.5 16h23" />
            </svg>
          </div>
          <strong className="browser-loading__title">正在连接并加载网页…</strong>
          {address && (
            <span className="browser-loading__url" title={address}>
              {address}
            </span>
          )}
        </div>
      )}
      {!page.url && !page.loading && <div className="browser-empty" role="status">
        <svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="11.5" /><ellipse cx="16" cy="16" rx="4.5" ry="11.5" /><path d="M4.5 16h23" /></svg>
        <strong>开始浏览</strong>
        <span>输入网址或搜索内容</span>
      </div>}
    </div>
  </section>;
}
