import { useEffect, useRef, useState } from "react";
import type { HookActivity, HookHistoryRequest } from "../../../../shared/hooks.js";
import { userFacingError } from "../../session-state.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { useDialogFocusLifecycle } from "../useDialogFocusLifecycle.js";
import "./PluginDetailDialog.css";
import "./HookSettings.css";

const labels = { intent: "等待执行", running: "执行中", completed: "已完成", failed: "失败", unknown: "结果未知", skipped: "已跳过" };

export function HookHistoryDialog({ open, sourceId, runId, onClose }: {
  open: boolean;
  sourceId?: string | undefined;
  runId?: string | undefined;
  onClose: () => void;
}) {
  const [items, setItems] = useState<HookActivity[]>([]);
  const [cursor, setCursor] = useState<number | null>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const revision = useRef(0);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocusLifecycle({ open, initialFocusRef: closeRef });

  async function load(next?: number | null, replace = false) {
    const epoch = ++revision.current;
    setLoading(true); setError(undefined);
    try {
      const request: HookHistoryRequest = { ...(sourceId ? { sourceId } : {}), ...(runId ? { runId } : {}), ...(next ? { cursor: next } : {}) };
      const page = await window.eidosRuntime.readHookHistory(request);
      if (epoch !== revision.current) return;
      setItems(current => replace ? page.items ?? [] : [...current, ...(page.items ?? [])]);
      setCursor(page.nextCursor);
    } catch (cause) {
      if (epoch === revision.current) setError(userFacingError(cause));
    } finally {
      if (epoch === revision.current) setLoading(false);
    }
  }

  useEffect(() => {
    if (open) { setItems([]); setCursor(undefined); void load(undefined, true); }
    return () => { revision.current++; };
  }, [open, sourceId, runId]);

  useEffect(() => {
    if (!open) return;
    function keydown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
      if (event.key !== "Tab") return;
      const targets = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled]), summary, [tabindex='0']") ?? [])
        .filter(element => element.checkVisibility?.({ checkVisibilityCSS: true }) ?? !element.closest("details:not([open]) > :not(summary)"));
      const first = targets[0], last = targets.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [open, onClose]);

  if (!open) return null;
  return <div className="modal-backdrop" onClick={onClose}>
    <div className="modal-dialog plugin-resource-dialog hook-history-dialog" ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="hook-history-title" onClick={event => event.stopPropagation()}>
      <header className="plugin-resource-header"><h2 id="hook-history-title">Hook 执行记录</h2><button type="button" className="skill-detail-close-btn" ref={closeRef} aria-label="关闭 Hook 执行记录" onClick={onClose}>×</button></header>
      <div className="plugin-resource-body" tabIndex={0} aria-label="Hook 执行记录列表" aria-busy={loading}>
        <Button variant="secondary" size="small" disabled={loading} onClick={() => void load(undefined, true)}>刷新</Button>
        {error && <AlertBanner tone="error">{error}</AlertBanner>}
        {!items.length && !loading && !error && <p>暂无执行记录。</p>}
        {loading && <p role="status">正在读取执行记录…</p>}
        {items.map(item => <details key={item.id} className="plugin-resource-hook">
          <summary>{item.event} · {labels[item.status]}{item.background ? " · 后台" : ""}</summary>
          <p>{new Date(item.createdAt).toLocaleString()}{item.result?.durationMs ? ` · ${item.result.durationMs} ms` : ""}</p>
          {item.statusMessage && <p>{item.statusMessage}</p>}
          {item.result?.systemMessage && <AlertBanner tone="warning">{item.result.systemMessage}</AlertBanner>}
          {(item.result?.message || item.result?.errorCode) && <p>{item.result.message || item.result.errorCode}</p>}
          {(item.stdoutPreview || item.stderrPreview) && <pre className="plugin-hook-command">{[item.stdoutPreview, item.stderrPreview].filter(Boolean).join("\n")}</pre>}
          {(item.previewTruncated || item.result?.contextTruncated) && <p>此处显示输出预览，部分内容已省略。</p>}
        </details>)}
        {cursor && <Button variant="secondary" size="small" disabled={loading} onClick={() => void load(cursor)}>加载更早记录</Button>}
      </div>
    </div>
  </div>;
}
