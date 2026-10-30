import { useEffect, useRef, useState } from "react";
import type { HookConfigurationRequest, HookSourceReview } from "../../../../shared/hooks.js";
import { userFacingError } from "../../session-state.js";
import { ToastContainer } from "../ToastContainer.js";
import { Button } from "../Button.js";
import { DropdownMenu, type DropdownMenuItem } from "../DropdownMenu.js";
import { ConfirmDialog } from "../ConfirmDialog.js";
import { SettingRow } from "../SettingRow.js";
import { SettingSection } from "../SettingSection.js";
import { StatusBadge } from "../StatusBadge.js";
import { Toggle } from "../Toggle.js";
import { HookHistoryDialog } from "./HookHistoryDialog.js";
import { ChevronDownIcon } from "./MemoryIcons.js";
import { CodeIcon, FinderIcon } from "../MenuIcons.js";

function getHookSourceTitle(source: HookSourceReview): string {
  if (source.scope === "user") {
    return "个人 Hook";
  }
  const firstPath = source.paths?.[0] || "";
  const parts = firstPath.split("/").filter(Boolean);
  const eidosIndex = parts.indexOf(".eidos");
  if (eidosIndex > 0 && parts[eidosIndex - 1]) {
    return parts[eidosIndex - 1]!;
  }
  if (parts.length >= 2 && parts[parts.length - 2]) {
    return parts[parts.length - 2]!;
  }
  return "项目";
}

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

  async function handleToggle(source: HookSourceReview, enabled: boolean) {
    const scope = scopeRevision.current;
    setBusy(true);
    setError(undefined);
    try {
      if (enabled) {
        if (!source.trusted) {
          await window.eidosRuntime.hookConfiguration({
            action: "trust",
            sourceId: source.id,
            contentHash: source.contentHash,
            trusted: true,
          });
        }
        await window.eidosRuntime.hookConfiguration({
          action: "enable",
          sourceId: source.id,
          contentHash: source.contentHash,
          enabled: true,
        });
      } else {
        await window.eidosRuntime.hookConfiguration({
          action: "enable",
          sourceId: source.id,
          contentHash: source.contentHash,
          enabled: false,
        });
      }
      if (scope !== scopeRevision.current) return;
      await load();
    } catch (cause) {
      if (scope === scopeRevision.current) setError(userFacingError(cause));
    } finally {
      if (scope === scopeRevision.current) setBusy(false);
    }
  }

  async function handleOpenFile(path: string) {
    try {
      if (typeof window.eidosRuntime?.openPath === "function") {
        await window.eidosRuntime.openPath(path);
      } else if (typeof window.eidosRuntime?.showItemInFolder === "function") {
        await window.eidosRuntime.showItemInFolder(path);
      }
    } catch (cause) {
      setError(userFacingError(cause));
    }
  }

  async function handleShowInFinder(path: string) {
    try {
      if (typeof window.eidosRuntime?.showItemInFolder === "function") {
        await window.eidosRuntime.showItemInFolder(path);
      }
    } catch (cause) {
      setError(userFacingError(cause));
    }
  }

  if (!available) return null;

  return (
    <>
      <div inert={Boolean(review || history)}>
        <SettingSection
          title="Hook"
          description="个人目录中的 hooks.json、config.toml，以及当前项目 .eidos 下的同名文件可以提供 Hook。"
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
              toasts={[{ id: `hook-source-error-${error}`, message: error, type: "error", duration: 4000 }]}
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
            const title = getHookSourceTitle(source);
            const isChecked = Boolean(source.trusted && source.enabled);
            const firstPath = source.paths?.[0] || "";
            const fileName = firstPath.split("/").filter(Boolean).at(-1) || "hooks.json";

            const menuItems: DropdownMenuItem[] = [
              {
                key: "open-file",
                label: `打开 ${fileName}`,
                icon: <CodeIcon />,
                disabled: !firstPath,
                onClick: () => {
                  void handleOpenFile(firstPath);
                },
              },
              {
                key: "show-in-finder",
                label: "在 Finder 中显示",
                icon: <FinderIcon />,
                disabled: !firstPath,
                onClick: () => {
                  void handleShowInFinder(firstPath);
                },
              },
            ];

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
                    <span className="hook-source-title-text">{title}</span>
                    {source.errorCode && (
                      <StatusBadge tone="danger" dot>
                        配置无效
                      </StatusBadge>
                    )}
                  </div>
                }
                description={
                  <div className="hook-source-paths-wrapper">
                    <span className="hook-source-paths">{source.paths.join("\n")}</span>
                  </div>
                }
                action={
                  <div className="hook-row-actions">
                    <Toggle
                      label={`启用或停用${title}`}
                      checked={isChecked}
                      disabled={busy || Boolean(source.errorCode)}
                      onChange={(enabled) => void handleToggle(source, enabled)}
                    />
                    <div className="hook-split-button-group">
                      <Button
                        variant="secondary"
                        size="small"
                        className="hook-split-button-main"
                        disabled={busy || Boolean(source.errorCode)}
                        onClick={() => setReview(source)}
                      >
                        查看
                      </Button>
                      <DropdownMenu
                        className="hook-split-button-dropdown"
                        triggerClassName="hook-split-button-caret"
                        triggerAriaLabel={`更多 ${title} 选项`}
                        align="right"
                        trigger={<ChevronDownIcon width={13} height={13} className="split-button-caret-icon" />}
                        items={menuItems}
                      />
                    </div>
                    <Button
                      variant="secondary"
                      size="small"
                      disabled={busy}
                      onClick={() => setHistory(source.id)}
                    >
                      执行记录
                    </Button>
                  </div>
                }
              />
            );
          })}
        </SettingSection>
      </div>

      <ConfirmDialog
        open={Boolean(review)}
        title="查看本地 Hook"
        busy={busy}
        error={error}
        confirmLabel="关闭"
        description={
          <div className="hook-review-container">
            <div className="hook-review-list">
              {review?.definitions?.map((hook) => (
                <div key={hook.key} className="hook-definition-card">
                  <div className="hook-definition-header">
                    <span className="hook-event-tag">{hook.event}</span>
                    <span className="hook-handler-type">{hook.handlerType}</span>
                    {hook.matcher && <span className="hook-matcher-tag">{hook.matcher}</span>}
                    {hook.background && <span className="hook-bg-tag">后台</span>}
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
        onConfirm={() => setReview(undefined)}
      />

      <HookHistoryDialog open={Boolean(history)} sourceId={history} onClose={() => setHistory(undefined)} />
    </>
  );
}
