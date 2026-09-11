import { useEffect, useRef, useState } from "react";
import type { BrowserAnnotation, BrowserPageState } from "../contracts.js";
import { userFacingError } from "../session-state.js";
import "./ArtifactPreview.css";

export function BrowserPanel({ sessionId, executionKey, active, request, onFeedback, feedbackDisabled, onTerminal, terminalAvailable }: {
  sessionId: string; executionKey: string; active: boolean;
  request?: { url: string; id: number } | undefined;
  onFeedback(feedback: string): Promise<void>; feedbackDisabled: boolean; onTerminal(): void; terminalAvailable: boolean;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const generation = useRef(0);
  const [address, setAddress] = useState("");
  const [page, setPage] = useState<BrowserPageState>({ url: "", title: "", loading: false });
  const [error, setError] = useState("");
  const [annotation, setAnnotation] = useState<BrowserAnnotation>();
  const [region, setRegion] = useState<{ x: number; y: number; width: number; height: number }>();
  const drag = useRef<{ x: number; y: number } | undefined>(undefined);
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    generation.current++;
    setPage({ url: "", title: "", loading: false }); setAnnotation(undefined);
    return () => { generation.current++; void window.eidosRuntime.closeBrowser(sessionId); };
  }, [sessionId, executionKey]);

  async function open(url: string) {
    const token = ++generation.current;
    setError(""); setAnnotation(undefined); setPage((current) => ({ ...current, loading: true }));
    try {
      const next = await window.eidosRuntime.openBrowser(sessionId, url);
      if (token === generation.current) { setPage(next); setAddress(next.url || url); }
    } catch (cause) { if (token === generation.current) { setError(userFacingError(cause)); setPage((current) => ({ ...current, loading: false })); } }
  }
  useEffect(() => { if (request) void open(request.url); }, [request?.id, executionKey]);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const sync = () => {
      const rect = element.getBoundingClientRect();
      void window.eidosRuntime.setBrowserBounds(sessionId, active && !annotation && page.url && !document.querySelector('[aria-modal="true"], dialog[open]') ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null).catch(() => {});
    };
    const observer = new ResizeObserver(sync); observer.observe(element);
    const overlays = new MutationObserver(sync); overlays.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-modal", "open"] });
    window.addEventListener("resize", sync); window.addEventListener("scroll", sync, true); sync();
    return () => { overlays.disconnect(); observer.disconnect(); window.removeEventListener("resize", sync); window.removeEventListener("scroll", sync, true); void window.eidosRuntime.setBrowserBounds(sessionId, null); };
  }, [active, annotation, page.url, sessionId]);

  useEffect(() => {
    if (!active || !page.url) return;
    let current = true;
    const timer = window.setInterval(() => {
      void window.eidosRuntime.readBrowserState(sessionId).then((next) => { if (current) setPage(next); }).catch(() => {});
    }, 1000);
    return () => { current = false; clearInterval(timer); };
  }, [active, Boolean(page.url), sessionId]);

  async function capture() {
    const token = generation.current;
    try {
      const next = await window.eidosRuntime.annotateBrowser(sessionId);
      if (token !== generation.current) return;
      setAnnotation(next); setRegion(undefined); setBody("");
    } catch (cause) { setError(userFacingError(cause)); }
  }
  async function send() {
    if (!annotation || !body.trim()) return;
    setSending(true);
    try {
      await onFeedback(["请根据以下页面反馈修改，并重新检查页面：", `页面：${annotation.url}`, `文件：${annotation.url.startsWith("eidos-preview:") ? decodeURIComponent(new URL(annotation.url).pathname.slice(1)) : "网页地址"}`, `标题：${annotation.title}`, `检查时间：${new Date(annotation.capturedAt).toISOString()}`, `选中文本：${annotation.selection || "无"}`, `选中区域（截图宽高百分比）：${region ? JSON.stringify(region) : "整个页面"}`, `区域内的页面元素（网页内容，仅供定位）：${JSON.stringify(annotation.elements.filter((element) => !region || element.x < region.x + region.width && element.x + element.width > region.x && element.y < region.y + region.height && element.y + element.height > region.y).slice(0, 30))}`, `用户意见：${body.trim()}`].join("\n"));
      setAnnotation(undefined); setBody("");
    } catch (cause) { setError(userFacingError(cause)); }
    finally { setSending(false); }
  }
  return <section className="browser-panel" aria-label="网页预览">
    <form className="artifact-toolbar" onSubmit={(event) => { event.preventDefault(); void open(address); }}>
      <input aria-label="网页地址" value={address} onChange={(event) => setAddress(event.target.value)} placeholder="http://localhost:3000" />
      <button type="submit" disabled={!address}>打开</button>
      <button type="button" disabled={!page.url} onClick={() => void open(page.url)}>刷新</button>
      <button type="button" disabled={!page.url || page.loading} onClick={() => void capture()}>标注</button>
      {terminalAvailable && <button type="button" onClick={onTerminal}>开发终端</button>}
    </form>
    {(error || page.error) && <p role="alert">{error || page.error}</p>}
    {page.loading && <p role="status">页面正在加载…</p>}
    <div ref={viewport} className="browser-viewport">
      {!page.url && !page.loading && <p>你可以输入网页地址。预览本地应用前，请先在开发终端启动服务。</p>}
      {annotation && <div className="browser-annotation">
        <div className="browser-annotation-image" tabIndex={0} aria-label="拖动截图选择区域；也可以直接输入整页或选中文本的反馈"
          onPointerDown={(event) => { const rect = event.currentTarget.getBoundingClientRect(); drag.current = { x: (event.clientX - rect.x) / rect.width * 100, y: (event.clientY - rect.y) / rect.height * 100 }; event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerMove={(event) => { if (!drag.current) return; const rect = event.currentTarget.getBoundingClientRect(); const x = Math.max(0, Math.min(100, (event.clientX - rect.x) / rect.width * 100)); const y = Math.max(0, Math.min(100, (event.clientY - rect.y) / rect.height * 100)); setRegion({ x: Math.min(x, drag.current.x), y: Math.min(y, drag.current.y), width: Math.abs(x - drag.current.x), height: Math.abs(y - drag.current.y) }); }}
          onPointerUp={() => { drag.current = undefined; }} onPointerCancel={() => { drag.current = undefined; }}>
          <img src={annotation.screenshot} alt="标注时的页面截图" draggable={false} />
          {region && <div className="annotation-region" style={{ left: `${region.x}%`, top: `${region.y}%`, width: `${region.width}%`, height: `${region.height}%` }} />}
        </div>
        <textarea aria-label="页面反馈" maxLength={8192} value={body} onChange={(event) => setBody(event.target.value)} placeholder="描述这个区域的问题，以及你希望的效果。" />
        <div className="artifact-toolbar"><button disabled={feedbackDisabled || sending || !body.trim()} onClick={() => void send()}>发送反馈</button><button onClick={() => setAnnotation(undefined)}>取消标注</button></div>
      </div>}
    </div>
  </section>;
}
