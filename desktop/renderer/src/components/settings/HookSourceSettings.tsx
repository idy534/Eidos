import { useEffect, useRef, useState } from "react";
import type { HookConfigurationRequest, HookSourceReview } from "../../../../shared/hooks.js";
import { userFacingError } from "../../session-state.js";
import { AlertBanner } from "../AlertBanner.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { Toggle } from "../Toggle.js";
import { HookHistoryDialog } from "./HookHistoryDialog.js";

export function HookSourceSettings({ sessionId }: { sessionId?: string | undefined }) {
  const [sources, setSources] = useState<HookSourceReview[]>([]);
  const [review, setReview] = useState<HookSourceReview>();
  const [history, setHistory] = useState<string>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const revision = useRef(0);
  const scopeRevision = useRef(0);
  const available = typeof window.eidosRuntime?.hookConfiguration === "function";

  async function load() {
    const epoch = ++revision.current;
    setError(undefined); setLoading(true);
    try {
      const response = await window.eidosRuntime.hookConfiguration({ action: "read", ...(sessionId ? { sessionId } : {}) });
      if (revision.current === epoch) setSources(response.sources ?? []);
    } catch (cause) {
      if (revision.current === epoch) setError(userFacingError(cause));
    } finally {
      if (revision.current === epoch) setLoading(false);
    }
  }
  useEffect(() => {
    scopeRevision.current++;
    setSources([]); setReview(undefined); setHistory(undefined); setBusy(false); setError(undefined);
    if (available) void load();
    return () => { revision.current++; scopeRevision.current++; };
  }, [sessionId, available]);

  async function change(source: HookSourceReview, request: HookConfigurationRequest) {
    const scope = scopeRevision.current;
    setBusy(true); setError(undefined);
    try {
      await window.eidosRuntime.hookConfiguration({ ...request, sourceId: source.id, contentHash: source.contentHash });
      if (scope !== scopeRevision.current) return;
      setReview(undefined);
      await load();
    } catch (cause) { if (scope === scopeRevision.current) setError(userFacingError(cause)); }
    finally { if (scope === scopeRevision.current) setBusy(false); }
  }

  if (!available) return null;
  return <>
    <div inert={Boolean(review || history)}>
      <SettingSection title="个人与项目 Hook">
        <p className="plugin-resource-empty">个人目录中的 hooks.json、config.toml，以及当前项目 .eidos 下的同名文件可以提供 Hook。配置变更后需要重新审阅。</p>
        <Button variant="secondary" size="small" disabled={busy || loading} onClick={() => void load()}>刷新配置</Button>
        {error && !review && <AlertBanner tone="error">{error}</AlertBanner>}
        {loading && <p role="status">正在读取 Hook 配置…</p>}
        {!sources.length && !error && !loading && <p>尚未发现本地 Hook 配置。</p>}
        {sources.map(source => <SettingRow key={source.id} title={source.scope === "user" ? "个人 Hook" : "项目 Hook"}
          description={<span className="hook-source-paths">{source.paths.join("\n")}</span>}
          action={<Toggle label={`启用或停用${source.scope === "user" ? "个人" : "项目"} Hook`} checked={source.enabled ?? true} disabled={busy}
            onChange={enabled => void change(source, { action: "enable", enabled })} />}>
          <p>{source.errorCode ? "配置无法读取或格式无效，请修正文件后刷新。" : source.trusted ? "当前配置已信任。" : "当前配置尚未信任，不会执行。"}</p>
          <div className="hook-source-actions">
            <Button variant="secondary" size="small" disabled={busy || Boolean(source.errorCode)} onClick={() => setReview(source)}>审阅 Hook</Button>
            <Button variant="secondary" size="small" onClick={() => setHistory(source.id)}>执行记录</Button>
            {source.trusted && <Button variant="secondary" size="small" disabled={busy} onClick={() => void change(source, { action: "trust", trusted: false })}>撤销信任</Button>}
          </div>
        </SettingRow>)}
      </SettingSection>
    </div>
    <ConfirmDialog open={Boolean(review)} title="审阅本地 Hook" isDestructive={!review?.trusted} busy={busy} error={error}
      confirmLabel={review?.trusted ? "关闭" : "信任此配置"} cancelLabel="取消"
      description={<div><p>这些处理器会在匹配事件时自动运行。系统会继续检查权限。</p>
        {review?.definitions?.map(hook => <div key={hook.key}><strong>{hook.event}{hook.matcher ? ` · ${hook.matcher}` : ""}{hook.background ? " · 后台" : ""}</strong>
          <pre className="plugin-hook-command">{hook.handlerType === "mcp_tool" ? JSON.stringify({ server: hook.mcpServer, tool: hook.mcpTool, input: hook.mcpInput }, null, 2) : hook.command || `处理器：${hook.handlerType}（不执行）`}</pre></div>)}</div>}
      onCancel={() => setReview(undefined)} onConfirm={() => { if (review?.trusted) setReview(undefined); else if (review) void change(review, { action: "trust", trusted: true }); }} />
    <HookHistoryDialog open={Boolean(history)} sourceId={history} onClose={() => setHistory(undefined)} />
  </>;
}
