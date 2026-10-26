import { useEffect, useRef, useState } from "react";
import type { HookConfigurationRequest, HookSourceReview } from "../../../../shared/hooks.js";
import { userFacingError } from "../../session-state.js";
import { ToastContainer } from "../ToastContainer.js";
import { Button } from "../Button.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { StatusBadge } from "../StatusBadge.js";
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
    setError(undefined);
    setLoading(true);
    try {
      const response = await window.eidosRuntime.hookConfiguration({
        action: "read",
        ...(sessionId ? { sessionId } : {}),
      });
      if (revision.current === epoch) setSources(response.sources ?? []);
    } catch (cause) {
      if (revision.current === epoch) setError(userFacingError(cause));
    } finally {
      if (revision.current === epoch) setLoading(false);
    }
  }

  useEffect(() => {
    scopeRevision.current++;
    setSources([]);
    setReview(undefined);
    setHistory(undefined);
    setBusy(false);
    setError(undefined);
    if (available) void load();
    return () => {
      revision.current++;
      scopeRevision.current++;
    };
  }, [sessionId, available]);

  async function change(source: HookSourceReview, request: HookConfigurationRequest) {
    const scope = scopeRevision.current;
    setBusy(true);
    setError(undefined);
    try {
      await window.eidosRuntime.hookConfiguration({
        ...request,
        sourceId: source.id,
        contentHash: source.contentHash,
      });
      if (scope !== scopeRevision.current) return;
      setReview(undefined);
      await load();
    } catch (cause) {
      if (scope === scopeRevision.current) setError(userFacingError(cause));
    } finally {
      if (scope === scopeRevision.current) setBusy(false);
    }
  }

  if (!available) return null;

  return (
    <>
      <div inert={Boolean(review || history)}>
        <SettingSection
          title="个人与项目 Hook"
          description="个人目录中的 hooks.json、config.toml，以及当前项目 .eidos 下的同名文件可以提供 Hook。配置变更后需要重新审阅。"
          headerAction={
            <Button
              variant="secondary"
              size="small"
              disabled={busy || loading}
              loading={loading}
              onClick={() => void load()}
            >
              刷新配置
            </Button>
          }
        >
          {error && !review && (
            <ToastContainer
              toasts={[{ id: "hook-source-error", message: error, type: "error", duration: 3500 }]}
              onDismiss={() => setError(undefined)}
            />
          )}

          {loading && !sources.length && (
            <div className="hook-loading-state" role="status">
              <div className="hook-spinner" />
              <span>正在读取 Hook 配置…</span>
            </div>
          )}

          {!sources.length && !error && !loading && (
            <div className="hook-empty-card">
              <div className="hook-empty-icon" aria-hidden="true">
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2v4a3 3 0 0 1-3 3H4M4 9l3-3M4 9l3 3" />
                </svg>
              </div>
              <p className="hook-empty-title">尚未发现本地 Hook 配置。</p>
              <p className="hook-empty-hint">
                可在个人配置目录或当前项目 <code>.eidos/</code> 目录下定义生命周期事件钩子，配置文件保存后点击上方刷新即可载入。
              </p>
            </div>
          )}

          {sources.map((source) => {
            const isUser = source.scope === "user";
            const statusTone = source.errorCode ? "danger" : source.trusted ? "success" : "warning";
            const statusLabel = source.errorCode ? "配置无效" : source.trusted ? "已信任" : "待信任";
            const statusText = source.errorCode
              ? "配置无法读取或格式无效，请修正文件后刷新。"
              : source.trusted
              ? "当前配置已信任。"
              : "当前配置尚未信任，不会执行。";
            const hookCount = source.definitions?.length ?? 0;

            return (
              <SettingRow
                key={source.id}
                className="hook-source-row"
                title={
                  <div className="hook-source-header">
                    <div
                      className={`hook-scope-badge ${isUser ? "hook-scope--user" : "hook-scope--project"}`}
                      aria-hidden="true"
                    >
                      {isUser ? (
                        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM2.5 14a5.5 5.5 0 0 1 11 0" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <path d="M1.5 3.5A1.5 1.5 0 0 1 3 2h3.2l1.6 1.6H13A1.5 1.5 0 0 1 14.5 5.1v7.4a1.5 1.5 0 0 1-1.5 1.5H3a1.5 1.5 0 0 1-1.5-1.5V3.5Z" />
                        </svg>
                      )}
                    </div>
                    <span className="hook-source-title-text">{isUser ? "个人 Hook" : "项目 Hook"}</span>
                    <StatusBadge tone={statusTone} dot>
                      {statusLabel}
                    </StatusBadge>
                    {hookCount > 0 && <span className="hook-count-tag">{hookCount} 个处理器</span>}
                  </div>
                }
                description={
                  <div className="hook-source-paths-wrapper">
                    <span className="hook-source-paths">{source.paths.join("\n")}</span>
                  </div>
                }
                action={
                  <Toggle
                    label={`启用或停用${isUser ? "个人" : "项目"} Hook`}
                    checked={source.enabled ?? true}
                    disabled={busy}
                    onChange={(enabled) => void change(source, { action: "enable", enabled })}
                  />
                }
              >
                <div className="hook-source-body-inner">
                  <div
                    className={`hook-source-status-callout ${
                      source.errorCode ? "status--error" : source.trusted ? "status--trusted" : "status--untrusted"
                    }`}
                  >
                    <span className="hook-status-dot" aria-hidden="true" />
                    <p>{statusText}</p>
                  </div>
                  <div className="hook-source-actions">
                    <Button
                      variant="secondary"
                      size="small"
                      disabled={busy || Boolean(source.errorCode)}
                      onClick={() => setReview(source)}
                    >
                      审阅 Hook
                    </Button>
                    <Button variant="secondary" size="small" onClick={() => setHistory(source.id)}>
                      执行记录
                    </Button>
                    {source.trusted && (
                      <Button
                        variant="secondary"
                        size="small"
                        disabled={busy}
                        onClick={() => void change(source, { action: "trust", trusted: false })}
                      >
                        撤销信任
                      </Button>
                    )}
                  </div>
                </div>
              </SettingRow>
            );
          })}
        </SettingSection>
      </div>

      <ConfirmDialog
        open={Boolean(review)}
        title="审阅本地 Hook"
        isDestructive={!review?.trusted}
        busy={busy}
        error={error}
        confirmLabel={review?.trusted ? "关闭" : "信任此配置"}
        cancelLabel="取消"
        description={
          <div className="hook-review-container">
            <p className="hook-review-lead">这些处理器会在匹配事件时自动运行。系统会继续检查权限。</p>
            <div className="hook-review-list">
              {review?.definitions?.map((hook) => (
                <div key={hook.key} className="hook-definition-card">
                  <div className="hook-definition-header">
                    <span className="hook-event-tag">{hook.event}</span>
                    {hook.matcher && <span className="hook-matcher-tag">{hook.matcher}</span>}
                    {hook.background && <span className="hook-bg-tag">后台</span>}
                    <span className="hook-handler-type">处理器：{hook.handlerType}</span>
                  </div>
                  <pre className="plugin-hook-command">
                    {hook.handlerType === "mcp_tool"
                      ? JSON.stringify({ server: hook.mcpServer, tool: hook.mcpTool, input: hook.mcpInput }, null, 2)
                      : hook.command || `处理器：${hook.handlerType}（不执行）`}
                  </pre>
                </div>
              ))}
            </div>
          </div>
        }
        onCancel={() => setReview(undefined)}
        onConfirm={() => {
          if (review?.trusted) setReview(undefined);
          else if (review) void change(review, { action: "trust", trusted: true });
        }}
      />

      <HookHistoryDialog open={Boolean(history)} sourceId={history} onClose={() => setHistory(undefined)} />
    </>
  );
}
