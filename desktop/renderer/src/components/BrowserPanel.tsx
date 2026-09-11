import { useEffect, useRef, useState } from "react";
import type { BrowserPageState } from "../contracts.js";
import { userFacingError } from "../session-state.js";
import "./ArtifactPreview.css";

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
      if (!candidate.username && !candidate.password && (host.includes(".") || isIpv4(host) || isLocalHost(host))) {
        return `${isLocalHost(host) ? "http" : "https"}://${value}`;
      }
    } catch {
      // Non-address input is sent to Google search.
    }
  }
  return `https://www.google.com/search?q=${encodeURIComponent(value)}`;
}

export function BrowserPanel({ browserId, sessionId, executionKey, active, request }: {
  browserId: string; sessionId: string; executionKey: string; active: boolean;
  request?: { url: string; id: number } | undefined;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const [address, setAddress] = useState("");
  const [page, setPage] = useState<BrowserPageState>({ url: "", title: "", loading: false });
  const [error, setError] = useState("");

  useEffect(() => {
    generation.current++;
    setPage({ url: "", title: "", loading: false });
    setAddress("");
    return () => { generation.current++; void window.eidosRuntime.closeBrowser(sessionId, browserId); };
  }, [browserId, sessionId, executionKey]);

  async function open(url: string) {
    const target = resolveBrowserTarget(url);
    if (!target) return;
    const token = ++generation.current;
    setError(""); setPage((current) => ({ ...current, loading: true }));
    try {
      const next = await window.eidosRuntime.openBrowser(sessionId, browserId, target);
      if (token === generation.current) { setPage(next); setAddress(next.url || target); }
    } catch (cause) { if (token === generation.current) { setError(userFacingError(cause)); setPage((current) => ({ ...current, loading: false })); } }
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
    if (!active || !page.url) return;
    let current = true;
    const timer = window.setInterval(() => {
      void window.eidosRuntime.readBrowserState(sessionId, browserId).then((next) => {
        if (!current) return;
        setPage(next);
        if (next.url) setAddress(next.url);
      }).catch(() => {});
    }, 1000);
    return () => { current = false; clearInterval(timer); };
  }, [active, Boolean(page.url), sessionId]);

  return <section className="browser-panel" aria-label="网页预览">
    <form className="browser-navigation" onSubmit={(event) => { event.preventDefault(); void open(address); }}>
      <div className="browser-navigation__history" aria-label="网页历史导航">
        <button type="button" className="browser-navigation__button" aria-label="后退" title="后退" disabled>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 5 7.5 10l5 5" /></svg>
        </button>
        <button type="button" className="browser-navigation__button" aria-label="前进" title="前进" disabled>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7.5 5 5 5-5 5" /></svg>
        </button>
        <button type="button" className="browser-navigation__button" aria-label="刷新" title="刷新" disabled={!page.url || page.loading} onClick={() => void open(page.url)}>
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16 10a6 6 0 1 1-1.76-4.24" /><path d="M16 4v4h-4" /></svg>
        </button>
      </div>
      <input
        aria-label="网页地址"
        className="browser-navigation__address"
        value={address}
        onChange={(event) => setAddress(event.target.value)}
        placeholder="搜索或输入网址"
      />
    </form>
    {(error || page.error) && <p role="alert">{error || page.error}</p>}
    {page.loading && <p role="status">页面正在加载…</p>}
    <div ref={viewport} className="browser-viewport">
      {!page.url && !page.loading && <div className="browser-empty" role="status">
        <svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="11.5" /><ellipse cx="16" cy="16" rx="4.5" ry="11.5" /><path d="M4.5 16h23" /></svg>
        <strong>开始浏览</strong>
        <span>输入网址或搜索内容</span>
      </div>}
    </div>
  </section>;
}
