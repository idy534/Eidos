import { useEffect, useRef, useState } from "react";
import type {
  MemoryEntry,
  MemoryManageRequest,
  MemorySettings as Settings,
  MemoryState,
} from "../../../../shared/memory.generated.js";
import { Button } from "../Button.js";
import { SettingSection } from "./SettingSection.js";
import { SettingRow } from "./SettingRow.js";
import { Toggle } from "./Toggle.js";
import { ConfirmDialog } from "./ConfirmDialog.js";
import { StatusBadge, type StatusTone } from "./StatusBadge.js";
import { EmptySettingsState } from "./EmptySettingsState.js";
import "./MemorySettings.css";

const statusLabels: Record<string, string> = {
  active: "已生效",
  candidate: "待确认",
  archived: "已归档",
  superseded: "历史版本",
  quarantined: "来源失效",
  forgotten: "已遗忘",
};

const jobLabels: Record<string, string> = {
  queued: "等待整理",
  running: "正在整理",
  succeeded: "已完成",
  retry_wait: "等待重试",
  paused_budget: "预算暂停",
  blocked_model: "模型配置不可用",
  superseded: "来源已更新",
  canceled: "已取消",
  failed: "整理失败",
};

const statusToneMap: Record<string, StatusTone> = {
  active: "success",
  candidate: "warning",
  archived: "neutral",
  superseded: "neutral",
  quarantined: "danger",
  forgotten: "danger",
};

const jobToneMap: Record<string, StatusTone> = {
  queued: "neutral",
  running: "info",
  succeeded: "success",
  retry_wait: "warning",
  paused_budget: "warning",
  blocked_model: "danger",
  superseded: "neutral",
  canceled: "neutral",
  failed: "danger",
};

export function MemorySettings({
  sessionId,
  onOpenSource,
}: {
  sessionId?: string | undefined;
  onOpenSource?: ((id: string) => void) | undefined;
}) {
  const [state, setState] = useState<MemoryState>();
  const [scope, setScope] = useState<"current" | "global">(sessionId ? "current" : "global");
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [history, setHistory] = useState(false);
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState<MemoryEntry>();
  const [editContent, setEditContent] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [confirmation, setConfirmation] = useState<{
    title: string;
    description: string;
    action: () => Promise<unknown>;
  }>();
  const generation = useRef(0);

  useEffect(() => {
    const token = ++generation.current;
    let canceled = false;
    let pending = false;
    async function load() {
      if (pending) return;
      pending = true;
      try {
        const value = await window.eidosRuntime.memory("memory/list", {
          sessionId: sessionId ?? null,
          scope,
          query,
          cursor,
          includeHistory: history,
        });
        if (!canceled && token === generation.current) setState(value);
      } catch {
        if (!canceled) setError("无法读取记忆，请检查 Runtime 状态。");
      } finally {
        pending = false;
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [sessionId, scope, query, cursor, history, refresh]);

  async function perform(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      setConfirmation(undefined);
      setEditing(undefined);
      setRefresh((value) => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "记忆操作失败，请刷新后重试。");
    } finally {
      setBusy(false);
    }
  }

  function manage(
    entry: MemoryEntry,
    action: MemoryManageRequest["action"],
    revised?: string,
  ) {
    return window.eidosRuntime.memory("memory/manage", {
      sessionId: sessionId ?? null,
      entryId: entry.id,
      expectedRevision: entry.revision,
      action,
      operationId: crypto.randomUUID(),
      ...(revised === undefined ? {} : { content: revised }),
    });
  }

  async function openCorrection(entry: MemoryEntry) {
    setBusy(true);
    setError("");
    const token = generation.current;
    try {
      // Search rows contain previews. Editing always starts from a full version.
      const result = await window.eidosRuntime.memory("memory/get", {
        sessionId: sessionId ?? null,
        entryId: entry.id,
      });
      if (token !== generation.current) return;
      setEditing(result.entry);
      setEditContent(result.entry.content);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取完整记忆，请刷新后重试。");
    } finally {
      setBusy(false);
    }
  }

  const selected = state?.scopes[0];

  function update(settings: Settings) {
    return perform(() =>
      window.eidosRuntime.memory("memory/settingsUpdate", {
        sessionId: sessionId ?? null,
        scope,
        settings,
      }),
    );
  }

  return (
    <div className="settings-panel memory-settings">
      <div className="settings-panel-header">
        <div className="header-title-with-stats">
          <div>
            <h1>记忆</h1>
            <p className="settings-panel-subtitle">
              保存偏好、决策和有来源的经验。自动学习默认关闭；开启后会将后续对话发送给所选模型整理，并产生模型费用。
            </p>
          </div>
          <div className="memory-scope-selector">
            <label className="memory-scope-label">
              <span className="memory-scope-tag">作用域</span>
              <select
                aria-label="记忆作用域"
                className="memory-scope-select"
                value={scope}
                disabled={busy}
                onChange={(event) => {
                  setScope(event.target.value as "current" | "global");
                  setState(undefined);
                  setCursor(0);
                }}
              >
                {sessionId && <option value="current">当前项目（无项目时为全局）</option>}
                <option value="global">全局</option>
              </select>
            </label>
          </div>
        </div>
      </div>

      {error && <p className="error-banner" role="alert">{error}</p>}

      {!selected ? (
        <div className="memory-loading-card">
          <div className="memory-loading-spinner" aria-hidden="true" />
          <p role="status">正在加载记忆…</p>
        </div>
      ) : (
        <>
          <SettingSection
            title="记忆策略与预算"
            description="配置模型记忆的读写权限，以及每日自动整理提炼的消耗配额限制。"
          >
            <SettingRow
              title="使用记忆"
              description="关闭后不再向模型提供本作用域记忆。"
              action={
                <Toggle
                  label="使用记忆"
                  checked={selected.settings.useEnabled ?? true}
                  disabled={busy}
                  onChange={(checked) =>
                    void update({ ...selected.settings, useEnabled: checked })
                  }
                />
              }
            />

            <SettingRow
              title="自动生成记忆"
              description="仅整理开启后的原始对话，后台失败不影响前台任务。"
              action={
                <Toggle
                  label="自动生成记忆"
                  checked={selected.settings.generateEnabled ?? false}
                  disabled={busy}
                  onChange={(checked) => {
                    if (checked) {
                      setConfirmation({
                        title: "开启自动学习",
                        description:
                          "后续对话将按当前模型配置发送给模型，最多执行提炼和整理两阶段请求。历史对话需单独选择回填。",
                        action: () =>
                          window.eidosRuntime.memory("memory/settingsUpdate", {
                            sessionId: sessionId ?? null,
                            scope,
                            settings: { ...selected.settings, generateEnabled: true },
                          }),
                      });
                    } else {
                      void update({ ...selected.settings, generateEnabled: false });
                    }
                  }}
                />
              }
            />

            <SettingRow
              title="每日后台预算"
              description="达到上限时暂停至下一天；无法取得真实用量时使用保守估计。"
            >
              <div className="memory-budget-grid">
                <div className="memory-budget-item">
                  <label className="memory-budget-label" htmlFor="memory-daily-call-limit">
                    请求次数
                  </label>
                  <div className="memory-budget-input-group">
                    <input
                      id="memory-daily-call-limit"
                      aria-label="每日记忆请求次数"
                      type="number"
                      min={0}
                      max={1000}
                      disabled={busy}
                      value={selected.settings.dailyCallLimit ?? 20}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        if (Number.isInteger(value) && value >= 0 && value <= 1000) {
                          void update({ ...selected.settings, dailyCallLimit: value });
                        }
                      }}
                    />
                    <span className="memory-budget-unit">次 / 天</span>
                  </div>
                </div>

                <div className="memory-budget-item">
                  <label className="memory-budget-label" htmlFor="memory-daily-token-limit">
                    Token 上限
                  </label>
                  <div className="memory-budget-input-group">
                    <input
                      id="memory-daily-token-limit"
                      aria-label="每日记忆 Token 上限"
                      type="number"
                      min={0}
                      max={10000000}
                      step={1000}
                      disabled={busy}
                      value={selected.settings.dailyTokenLimit ?? 100000}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        if (Number.isInteger(value) && value >= 0 && value <= 10000000) {
                          void update({ ...selected.settings, dailyTokenLimit: value });
                        }
                      }}
                    />
                    <span className="memory-budget-unit">Tokens / 天</span>
                  </div>
                </div>
              </div>
            </SettingRow>

            {sessionId && (
              <SettingRow
                title="临时对话"
                description="此对话不使用或生成记忆；已有依赖此对话的自动记忆会失效。"
                action={
                  <Toggle
                    label="临时对话"
                    checked={state?.temporary ?? false}
                    disabled={busy}
                    onChange={(temporary) =>
                      void perform(() =>
                        window.eidosRuntime.memory("memory/temporary", {
                          sessionId,
                          temporary,
                        }),
                      )
                    }
                  />
                }
              />
            )}

            {sessionId && (
              <SettingRow
                title="历史对话回填"
                description="整理当前对话历史。原始内容会发送给模型并计入后台预算；已遗忘的来源不会重新学习。"
                action={
                  <Button
                    variant="secondary"
                    size="small"
                    disabled={busy || !selected.settings.generateEnabled || state?.temporary}
                    onClick={() =>
                      setConfirmation({
                        title: "整理当前对话历史",
                        description:
                          "仅回填当前选中的对话。原始内容会发送给模型并计入后台预算；已遗忘的来源不会重新学习。",
                        action: () =>
                          window.eidosRuntime.memory("memory/backfill", {
                            sessionId,
                            operationId: crypto.randomUUID(),
                          }),
                      })
                    }
                  >
                    回填当前对话
                  </Button>
                }
              />
            )}
          </SettingSection>

          <SettingSection
            title="新增记忆"
            description="手动向当前作用域记录规则、项目约定或偏好事实。"
          >
            <form
              className="memory-add-form"
              onSubmit={(event) => {
                event.preventDefault();
                void perform(async () => {
                  await window.eidosRuntime.memory("memory/remember", {
                    sessionId: sessionId ?? null,
                    operationId: crypto.randomUUID(),
                    scope,
                    content,
                  });
                  setContent("");
                });
              }}
            >
              <div className="memory-textarea-wrapper">
                <textarea
                  aria-label="新增记忆内容"
                  className="memory-add-textarea"
                  placeholder="例如：在当前项目中，所有组件优先采用 TypeScript 严格模式，样式使用 CSS Variables…"
                  maxLength={8192}
                  value={content}
                  disabled={busy}
                  onChange={(event) => setContent(event.target.value)}
                />
                <div className="memory-form-footer">
                  <span className="memory-char-count">{content.length} / 8192</span>
                  <div className="memory-form-actions">
                    {content.trim() && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="small"
                        disabled={busy}
                        onClick={() => setContent("")}
                      >
                        清空
                      </Button>
                    )}
                    <Button
                      type="submit"
                      variant="primary"
                      size="small"
                      disabled={busy || !content.trim()}
                    >
                      记住
                    </Button>
                  </div>
                </div>
              </div>
            </form>
          </SettingSection>

          <SettingSection
            className="memory-library-section"
            title="记忆库"
            description="浏览、检索并管理当前作用域中已沉淀的记忆与经验。"
            headerAction={
              <span className="memory-engine-badge">
                {state?.trigramAvailable ? "支持中文片段和英文词检索" : "使用词检索和有界短词检索"}
              </span>
            }
          >
            <div className="memory-filter-bar">
              <div className="memory-search-wrapper">
                <svg
                  className="memory-search-icon"
                  viewBox="0 0 16 16"
                  width="14"
                  height="14"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <circle cx="7" cy="7" r="4.5" />
                  <path d="M10.5 10.5L14 14" />
                </svg>
                <input
                  aria-label="搜索记忆"
                  type="text"
                  className="memory-search-input"
                  placeholder="搜索记忆内容或关键词…"
                  maxLength={512}
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setCursor(0);
                  }}
                />
                {query && (
                  <button
                    type="button"
                    className="memory-search-clear"
                    aria-label="清除搜索"
                    onClick={() => {
                      setQuery("");
                      setCursor(0);
                    }}
                  >
                    ✕
                  </button>
                )}
              </div>

              <div className="memory-filter-meta">
                <label className="memory-history-toggle">
                  <input
                    type="checkbox"
                    checked={history}
                    onChange={(event) => {
                      setHistory(event.target.checked);
                      setCursor(0);
                    }}
                  />
                  <span>显示归档和历史状态</span>
                </label>
              </div>
            </div>

            {state?.entries.length === 0 ? (
              <div className="memory-empty-state">
                <EmptySettingsState
                  title="没有符合条件的记忆。"
                  description={
                    query.trim()
                      ? "没有匹配当前关键词的记忆，请尝试更改搜索词或勾选显示历史状态。"
                      : "当前作用域下暂无有效记忆，可在上方手动添加一条偏好或规则。"
                  }
                />
              </div>
            ) : (
              <div className="memory-entry-list">
                {state?.entries.map((entry) => {
                  const tone = statusToneMap[entry.status] ?? "neutral";
                  return (
                    <article className="memory-entry" key={entry.id}>
                      <div className="memory-entry-header">
                        <div className="memory-entry-title-wrap">
                          <h3>
                            {entry.title || entry.kind}
                            <small className="memory-entry-subheading">
                              {statusLabels[entry.status]} · v{entry.revision}
                              {entry.pinned ? " · 已固定" : ""}
                            </small>
                          </h3>
                        </div>
                        <div className="memory-entry-badge-row">
                          <StatusBadge tone={tone}>{statusLabels[entry.status]}</StatusBadge>
                          <span className="memory-tag-rev">v{entry.revision}</span>
                          {entry.pinned && <span className="memory-tag-pinned">📌 已固定</span>}
                        </div>
                      </div>

                      <p className="memory-content">{entry.content}</p>

                      {entry.status === "superseded" && (
                        <div className="memory-superseded-banner">
                          <span>⚠️ 此记忆已由更新的版本替代</span>
                          <Button
                            size="small"
                            variant="ghost"
                            onClick={() => {
                              setQuery(entry.id);
                              setHistory(false);
                              setCursor(0);
                            }}
                          >
                            查看当前版本
                          </Button>
                        </div>
                      )}

                      <div className="memory-entry-footer">
                        <div className="memory-entry-metadata">
                          <span className="memory-meta-provenance">
                            {entry.userOwned ? "用户独立保存" : `来源：${entry.evidenceClass}`}
                          </span>
                          <span className="memory-meta-divider">·</span>
                          <span>{new Date(entry.updatedAt).toLocaleString()}</span>
                          {entry.validFrom != null && (
                            <>
                              <span className="memory-meta-divider">·</span>
                              <span>
                                有效期：{new Date(entry.validFrom).toLocaleDateString()} —{" "}
                                {entry.validTo == null
                                  ? "未设结束日期"
                                  : new Date(entry.validTo).toLocaleDateString()}
                              </span>
                            </>
                          )}
                          {entry.evidence.map((source) => (
                            <Button
                              key={source.itemId}
                              size="small"
                              variant="ghost"
                              className="memory-evidence-btn"
                              disabled={!onOpenSource}
                              onClick={() => onOpenSource?.(source.sessionId)}
                            >
                              查看来源对话
                            </Button>
                          ))}
                        </div>

                        <div className="memory-entry-actions">
                          {entry.status === "candidate" && (
                            <Button
                              size="small"
                              variant="primary"
                              disabled={busy}
                              onClick={() => void perform(() => manage(entry, "accept"))}
                            >
                              确认生效
                            </Button>
                          )}
                          <Button
                            size="small"
                            variant="secondary"
                            disabled={busy || entry.status === "superseded"}
                            onClick={() => void openCorrection(entry)}
                          >
                            纠正
                          </Button>
                          <Button
                            size="small"
                            variant="ghost"
                            disabled={busy || entry.status === "superseded"}
                            onClick={() =>
                              void perform(() =>
                                manage(entry, entry.pinned ? "unpin" : "pin"),
                              )
                            }
                          >
                            {entry.pinned ? "取消固定" : "固定"}
                          </Button>
                          {entry.status !== "archived" && (
                            <Button
                              size="small"
                              variant="ghost"
                              disabled={busy || entry.status === "superseded"}
                              onClick={() => void perform(() => manage(entry, "archive"))}
                            >
                              归档
                            </Button>
                          )}
                          <Button
                            size="small"
                            variant="danger"
                            disabled={busy || entry.status === "superseded"}
                            onClick={() =>
                              setConfirmation({
                                title: "遗忘这条记忆",
                                description:
                                  "删除所有正文版本并撤销相关上下文。同一来源不会再次自动学习；原始聊天由你单独管理。旧备份或已发送给模型的内容不能由此撤回。",
                                action: () => manage(entry, "forget"),
                              })
                            }
                          >
                            遗忘
                          </Button>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}

            <div className="memory-pagination">
              <Button
                variant="ghost"
                size="small"
                disabled={!cursor || busy}
                onClick={() => setCursor(0)}
              >
                返回第一页
              </Button>
              {state?.truncated && state.nextCursor != null && (
                <Button
                  variant="secondary"
                  size="small"
                  disabled={busy}
                  onClick={() => setCursor(state.nextCursor ?? 0)}
                >
                  下一页
                </Button>
              )}
            </div>
          </SettingSection>

          <SettingSection
            title="数据管理与维护"
            description="导出明文记录、生成数据完整备份或在索引异常时触发重建。"
          >
            <div className="memory-tools-grid">
              <div className="memory-tool-card">
                <div className="memory-tool-info">
                  <h4>导出 Markdown</h4>
                  <p>将当前页包含的记忆导出为便携的 Markdown 明文文档。</p>
                </div>
                <Button
                  variant="secondary"
                  size="small"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      const exported = await window.eidosRuntime.memory("memory/export", {
                        sessionId: sessionId ?? null,
                        scope,
                        query,
                        cursor,
                        includeHistory: history,
                      });
                      const url = URL.createObjectURL(
                        new Blob([exported.markdown], {
                          type: "text/markdown;charset=utf-8",
                        }),
                      );
                      const anchor = document.createElement("a");
                      anchor.href = url;
                      anchor.download = "eidos-memories.md";
                      anchor.click();
                      URL.revokeObjectURL(url);
                    })
                  }
                >
                  导出当前页（明文）
                </Button>
              </div>

              <div className="memory-tool-card">
                <div className="memory-tool-info">
                  <h4>完整本地备份</h4>
                  <p>包含 SQLite 状态、有效记忆与对话上下文，打包为未加密 ZIP。</p>
                </div>
                <Button
                  variant="secondary"
                  size="small"
                  disabled={busy}
                  onClick={() =>
                    setConfirmation({
                      title: "备份记忆和对话",
                      description:
                        "备份包含 SQLite 状态、所有有效记忆版本及对话上下文，保存为未加密 ZIP。请妥善保管；旧备份可能恢复已经遗忘的内容。恢复需要退出应用并使用新的数据目录。",
                      action: () => window.eidosRuntime.backupMemory(),
                    })
                  }
                >
                  完整备份（明文 ZIP）
                </Button>
              </div>

              <div className="memory-tool-card">
                <div className="memory-tool-info">
                  <h4>重建检索索引</h4>
                  <p>若遇到搜索结果缺失或异常，可重新构建 SQLite 全文与分词索引。</p>
                </div>
                <Button
                  variant="ghost"
                  size="small"
                  disabled={busy}
                  onClick={() =>
                    void perform(() =>
                      window.eidosRuntime.memory("memory/rebuild", {
                        sessionId: sessionId ?? null,
                      }),
                    )
                  }
                >
                  重建检索索引
                </Button>
              </div>
            </div>
          </SettingSection>

          <SettingSection
            title="后台整理"
            description="监控模型提炼、归纳与整理任务队列的状态与用量。"
          >
            {!state?.jobs || state.jobs.length === 0 ? (
              <p className="memory-no-jobs">当前暂无后台任务。</p>
            ) : (
              <ul className="memory-jobs-list">
                {state.jobs.map((job) => {
                  const jobTone = jobToneMap[job.state] ?? "neutral";
                  return (
                    <li className="memory-job-item" key={job.id}>
                      <div className="memory-job-left">
                        <StatusBadge tone={jobTone}>{jobLabels[job.state]}</StatusBadge>
                        <span className="memory-job-model">{job.modelId}</span>
                        <span className="memory-job-tokens">
                          {job.tokens} tokens{job.estimatedUsage ? "（估计）" : ""}
                        </span>
                        {job.errorCode && (
                          <span className="memory-job-error">· {job.errorCode}</span>
                        )}
                      </div>
                      {["failed", "blocked_model", "retry_wait", "paused_budget"].includes(
                        job.state,
                      ) && (
                        <Button
                          size="small"
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            void perform(() =>
                              window.eidosRuntime.memory("memory/jobsRetry", {
                                sessionId: sessionId ?? null,
                                jobId: job.id,
                              }),
                            )
                          }
                        >
                          确认当前模型配置并重试
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </SettingSection>
        </>
      )}

      <ConfirmDialog
        open={confirmation !== undefined}
        title={confirmation?.title ?? ""}
        description={confirmation?.description ?? ""}
        busy={busy}
        error={error || undefined}
        isDestructive={confirmation?.title.startsWith("遗忘") ?? false}
        onCancel={() => setConfirmation(undefined)}
        onConfirm={() => {
          if (confirmation) void perform(confirmation.action);
        }}
      />

      <ConfirmDialog
        open={editing !== undefined}
        title="纠正记忆"
        description={
          <label className="memory-edit-label">
            <span className="memory-edit-caption">正文</span>
            <textarea
              aria-label="纠正记忆正文"
              className="memory-edit-textarea"
              value={editContent}
              maxLength={8192}
              rows={6}
              onChange={(event) => setEditContent(event.target.value)}
            />
          </label>
        }
        busy={busy}
        error={error || undefined}
        onCancel={() => setEditing(undefined)}
        onConfirm={() => {
          if (editing && editContent.trim()) {
            void perform(() => manage(editing, "correct", editContent));
          }
        }}
      />
    </div>
  );
}
