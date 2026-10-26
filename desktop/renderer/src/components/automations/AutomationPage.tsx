import { useCallback, useEffect, useRef, useState } from "react";
import type { Automation, AutomationOccurrence, AutomationDesktopRequest, AutomationSummary } from "../../../../shared/automation.js";
import type { ModelOption, Project, Session } from "../../contracts.js";
import { Button } from "../Button.js";
import { ToastContainer, type ToastItem, type ToastType } from "../ToastContainer.js";
import { AutomationForm, type InitialAutomationValues } from "./AutomationForm.js";
import { AutomationTaskList } from "./AutomationTaskList.js";
import { userFacingError } from "../../session-state.js";
import { formatAutomationTime } from "./automation-time.js";

interface Props {
  projects: Project[];
  sessions: Session[];
  models: ModelOption[];
  currentSessionId?: string | undefined;
  currentProjectId?: string | undefined;
  runtimeVersion?: string | undefined;
  initialAutomationId?: string | undefined;
  onClose: () => void;
  onOpenSession: (id: string) => void;
}

interface AutomationTemplate {
  id: string;
  icon: string;
  title: string;
  desc: string;
  prompt: string;
  scheduleKind: "cron" | "at";
  expression: string;
}

const TEMPLATES: AutomationTemplate[] = [
  {
    id: "daily-why",
    icon: "💡",
    title: "每日一个为什么",
    desc: "随机挑选一个有趣的冷知识或生活百科问题，并给出详细、有趣、通俗易懂的解答...",
    prompt: "随机挑选一个有趣的冷知识或生活百科问题，并给出详细、有趣、通俗易懂的解答。每次问题尽量不重复，覆盖科学、生活、历史、自然、食物、文化、动物、人体等多个领域；先抛出问题再揭晓答案；语气轻松有趣；答案控制在 200-300 字。",
    scheduleKind: "cron",
    expression: "0 9 * * *",
  },
  {
    id: "ai-news",
    icon: "📰",
    title: "每日 AI 新闻推送",
    desc: "关注当天 AI 领域的重要动态，侧重 AI coding 与具身智能方向。筛选 3-5 条有价值的信息...",
    prompt: "关注当天 AI 领域的重要动态，侧重 AI coding 与具身智能方向。筛选 3-5 条有价值的信息，简要说明事件内容及值得关注的原因。结合 memory.md 里之前推送过的内容，尽量不要重复。",
    scheduleKind: "cron",
    expression: "0 9 * * *",
  },
  {
    id: "email-monitor",
    icon: "✉️",
    title: "监控电子邮箱",
    desc: "查看我的邮件，并告知我需要关注的事项。",
    prompt: "查看我的待处理邮件，总结重要发件人的核心诉求与待办事项，并告知我需要立即关注的事项。",
    scheduleKind: "cron",
    expression: "0 9 * * *",
  },
  {
    id: "weekly-report",
    icon: "📋",
    title: "每周工作周报",
    desc: "梳理本周仓库中的 PR 与 Issue 情况，包括新增、已关闭及重点讨论。输出主要进展与待关注事项。",
    prompt: "梳理本周仓库中的 PR 与 Issue 情况，包括新增、已关闭及重点讨论。输出一份周报，涵盖主要进展、关键变更和待关注事项。",
    scheduleKind: "cron",
    expression: "0 18 * * 5",
  },
  {
    id: "diet-plan",
    icon: "🥗",
    title: "每周饮食计划",
    desc: "规划一周的餐食，并列出食材采购清单。",
    prompt: "规划未来一周的健康餐食搭配与营养建议，并生成详细的周度食材采购清单。",
    scheduleKind: "cron",
    expression: "0 18 * * 0",
  },
  {
    id: "travel-flights",
    icon: "✈️",
    title: "为旅行搜索航班",
    desc: "为即将启程的旅行寻找最划算的机票。",
    prompt: "为即将启程的旅行目的地搜索航线与比价信息，分析不同日期和航空公司的价格走势，寻找最划算的机票方案。",
    scheduleKind: "cron",
    expression: "0 10 * * *",
  },
];

function ClockGraphic() {
  return (
    <svg className="automation-hero-clock" width="68" height="68" viewBox="0 0 68 68" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <defs>
        <radialGradient id="clockBezel" cx="34" cy="34" r="34" gradientUnits="userSpaceOnUse">
          <stop offset="0.75" stopColor="#E2E4E8"/>
          <stop offset="0.95" stopColor="#B4B8C2"/>
          <stop offset="1" stopColor="#90949F"/>
        </radialGradient>
        <linearGradient id="clockFace" x1="16" y1="8" x2="52" y2="60" gradientUnits="userSpaceOnUse">
          <stop stopColor="#FFFFFF"/>
          <stop offset="1" stopColor="#F5F6F8"/>
        </linearGradient>
        <filter id="clockShadow" x="0" y="2" width="68" height="66" filterUnits="userSpaceOnUse">
          <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="rgba(0,0,0,0.12)"/>
        </filter>
      </defs>
      <g filter="url(#clockShadow)">
        <circle cx="34" cy="33" r="30" fill="url(#clockBezel)" stroke="#B8BAC0" strokeWidth="0.5"/>
        <circle cx="34" cy="33" r="26.5" fill="none" stroke="rgba(0,0,0,0.08)" strokeWidth="1"/>
        <circle cx="34" cy="33" r="26" fill="url(#clockFace)"/>
        <line x1="34" y1="9.5" x2="34" y2="12.5" stroke="#717682" strokeWidth="1.5" strokeLinecap="round"/>
        <line x1="34" y1="53.5" x2="34" y2="56.5" stroke="#717682" strokeWidth="1.5" strokeLinecap="round"/>
        <line x1="10.5" y1="33" x2="13.5" y2="33" stroke="#717682" strokeWidth="1.5" strokeLinecap="round"/>
        <line x1="54.5" y1="33" x2="57.5" y2="33" stroke="#717682" strokeWidth="1.5" strokeLinecap="round"/>
        <line x1="34" y1="33" x2="23" y2="22" stroke="#2C3038" strokeWidth="2.5" strokeLinecap="round"/>
        <line x1="34" y1="33" x2="47" y2="21" stroke="#2C3038" strokeWidth="2" strokeLinecap="round"/>
        <line x1="34" y1="38" x2="34" y2="13" stroke="#FF5935" strokeWidth="1" strokeLinecap="round"/>
        <circle cx="34" cy="33" r="2" fill="#2C3038"/>
        <circle cx="34" cy="33" r="1" fill="#FF5935"/>
      </g>
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <path d="M8 3.5v9M3.5 8h9" />
    </svg>
  );
}

export function AutomationPage({
  projects,
  sessions,
  models,
  currentSessionId,
  currentProjectId,
  runtimeVersion,
  initialAutomationId,
  onClose,
  onOpenSession,
}: Props) {
  const [items, setItems] = useState<AutomationSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<Automation | null>(null);
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [templateValues, setTemplateValues] = useState<InitialAutomationValues | undefined>(undefined);
  const [history, setHistory] = useState<AutomationOccurrence[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);

  const selection = useRef(0);
  const listVersion = useRef(0);
  const mounted = useRef(true);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback((message: string, type: ToastType = "info", duration = 3500) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setToasts((prev) => [
      ...prev.filter((t) => !(t.type === type && t.message === message)),
      { id, type, message, duration },
    ]);
  }, []);

  const clearErrors = useCallback(() => {
    setToasts((prev) => prev.filter((t) => t.type !== "error"));
  }, []);

  // Close with Escape key matching SettingsPage
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (
        event.key === "Escape"
        && !document.querySelector("[role='dialog'], [role='alertdialog']")
      ) {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const refresh = useCallback(async (after?: string) => {
    const version = ++listVersion.current;
    try {
      const result = await window.eidosRuntime.automation({
        command: { mode: "view", scope: "all", limit: 20, ...(after ? { cursor: after } : {}) },
      });
      if (!mounted.current || version !== listVersion.current) return;
      setItems((current) => (after ? [...current, ...(result.items ?? [])] : result.items ?? []));
      setCursor(result.nextCursor ?? null);
    } catch (failure) {
      if (mounted.current && version === listVersion.current) showToast(userFacingError(failure), "error");
    } finally {
      if (mounted.current && version === listVersion.current) setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    const unsubscribe = window.eidosRuntime.onNotification((notification) => {
      if (notification.method === "automation/changed" || notification.method === "run/completed") void refresh();
    });
    return () => {
      mounted.current = false;
      selection.current++;
      unsubscribe();
    };
  }, [refresh]);

  useEffect(() => {
    if (!selected?.id) return;
    const id = selected.id;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = window.eidosRuntime.onNotification((notification) => {
      if (
        !(
          (notification.method === "automation/changed" && notification.params.id === id)
          || notification.method === "run/completed"
          || notification.method === "run/updated"
        )
      )
        return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        void Promise.all([
          window.eidosRuntime.automationHistory({ id }),
          editing ? Promise.resolve(null) : window.eidosRuntime.automation({ command: { mode: "view", id } }),
        ])
          .then(([records, detail]) => {
            if (disposed) return;
            setHistory(records.occurrences ?? []);
            setHistoryCursor(records.nextCursor ?? null);
            if (detail?.automation) setSelected(detail.automation);
          })
          .catch((failure) => {
            if (!disposed) showToast(userFacingError(failure), "error");
          });
      }, 50);
    });
    return () => {
      disposed = true;
      clearTimeout(timer);
      unsubscribe();
    };
  }, [selected?.id, editing, showToast]);

  const read = useCallback(async (id: string) => {
    const version = ++selection.current;
    clearErrors();
    setCreating(false);
    setEditing(false);
    setTemplateValues(undefined);
    setBusy(true);
    try {
      const [detail, records] = await Promise.all([
        window.eidosRuntime.automation({ command: { mode: "view", id } }),
        window.eidosRuntime.automationHistory({ id }),
      ]);
      if (version !== selection.current) return;
      setSelected(detail.automation ?? null);
      setHistory(records.occurrences ?? []);
      setHistoryCursor(records.nextCursor ?? null);
    } catch (failure) {
      if (version === selection.current) showToast(userFacingError(failure), "error");
    } finally {
      if (version === selection.current) setBusy(false);
    }
  }, [clearErrors, showToast]);

  useEffect(() => {
    if (initialAutomationId) void read(initialAutomationId);
  }, [initialAutomationId, read]);

  const save = async (command: AutomationDesktopRequest, confirmation?: "full-access-v1") => {
    setBusy(true);
    clearErrors();
    try {
      const result = await window.eidosRuntime.automation({
        operationId: crypto.randomUUID(),
        command,
        ...(confirmation ? { fullAccessConfirmation: confirmation } : {}),
        ...(currentSessionId ? { contextSessionId: currentSessionId } : {}),
      });
      setSelected(result.automation ?? null);
      setCreating(false);
      setEditing(false);
      setTemplateValues(undefined);
      await refresh();
    } catch (failure) {
      showToast(userFacingError(failure), "error");
      throw failure;
    } finally {
      setBusy(false);
    }
  };

  const mutate = async (mode: "delete" | "update") => {
    if (!selected) return;
    try {
      await save({
        mode,
        id: selected.id,
        expectedRevision: selected.revision,
        ...(mode === "update" ? { changes: { status: selected.status === "ACTIVE" ? "PAUSED" : "ACTIVE" } } : {}),
      });
    } catch {
      /* The shared action displays the error and keeps the selected version. */
    }
  };

  const runNow = async () => {
    if (!selected) return;
    setBusy(true);
    clearErrors();
    try {
      await window.eidosRuntime.automationRunNow({ id: selected.id, operationId: crypto.randomUUID() });
      await read(selected.id);
    } catch (failure) {
      showToast(userFacingError(failure), "error");
    } finally {
      setBusy(false);
    }
  };

  const moreHistory = async () => {
    if (!selected || !historyCursor) return;
    const id = selected.id;
    const version = selection.current;
    try {
      const result = await window.eidosRuntime.automationHistory({ id, cursor: historyCursor });
      if (version !== selection.current) return;
      setHistory((current) => [...current, ...(result.occurrences ?? [])]);
      setHistoryCursor(result.nextCursor ?? null);
    } catch (failure) {
      showToast(userFacingError(failure), "error");
    }
  };

  const handleApplyTemplate = (template: AutomationTemplate) => {
    selection.current++;
    setSelected(null);
    setEditing(false);
    setHistory([]);
    setBusy(false);
    setTemplateValues({
      name: template.title,
      prompt: template.prompt,
      expression: template.expression,
      kind: template.scheduleKind,
      approvalMode: "auto_review",
    });
    setCreating(true);
  };

  return (
    <div className="automation-page-wrapper">
      <ToastContainer
        toasts={toasts}
        onDismiss={dismissToast}
      />
      <header className="automation-page-header">
        <div className="header-left">
          <Button
            variant="secondary"
            size="small"
            onClick={onClose}
            aria-label="返回 Eidos"
          >
            ← 返回 Eidos
          </Button>
        </div>
        <div className="header-center">
          <span className="settings-header-title">定时任务</span>
        </div>
        <div className="header-right">
          <span className="version-pill">Eidos {runtimeVersion ?? "0.x"}</span>
        </div>
      </header>

      <main className="automation-page-body">
        <div className="automation-container">
          <aside className="automation-sidebar-col" aria-label="定时任务列表">
            <div className="automation-sidebar-header">
              <h2 className="automation-sidebar-title">定时任务</h2>
              <button
                type="button"
                className={`automation-icon-btn${showSearch ? " active" : ""}`}
                aria-label="搜索任务"
                title="搜索任务"
                onClick={() => {
                  setShowSearch((prev) => !prev);
                  if (showSearch) setSearchQuery("");
                }}
              >
                <SearchIcon />
              </button>
            </div>

            {showSearch && (
              <div className="automation-search-row">
                <input
                  type="search"
                  className="automation-search-input"
                  placeholder="搜索任务..."
                  aria-label="搜索定时任务"
                  value={searchQuery}
                  autoFocus
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>
            )}

            <button
              type="button"
              disabled={busy}
              className="automation-create-btn"
              onClick={() => {
                selection.current++;
                setCreating(true);
                setSelected(null);
                setEditing(false);
                setTemplateValues(undefined);
                setHistory([]);
                setBusy(false);
              }}
            >
              <PlusIcon />
              <span>新建任务</span>
            </button>

            <div className="automation-list-scroll">
              {loading ? (
                <p className="automation-status-text" role="status">正在读取任务…</p>
              ) : (
                <AutomationTaskList items={items} searchQuery={searchQuery}
                  selectedId={creating ? undefined : selected?.id} busy={busy} hasMore={Boolean(cursor)}
                  onSelect={(id) => void read(id)} />
              )}
              {cursor && (
                <div className="automation-more-wrap">
                  <Button size="small" variant="secondary" onClick={() => void refresh(cursor)}>
                    加载更多任务
                  </Button>
                </div>
              )}
            </div>
          </aside>

          <section className="automation-content-col" aria-label="任务详情">
            {creating || (editing && selected) ? (
              <div className="automation-form-view">
                <AutomationForm
                  key={creating ? `new:${templateValues?.name ?? ""}` : `${selected?.id}:${selected?.revision}`}
                  task={creating ? undefined : selected ?? undefined}
                  initialValues={creating ? templateValues : undefined}
                  projects={projects}
                  sessions={sessions}
                  models={models}
                  currentSessionId={currentSessionId}
                  currentProjectId={currentProjectId}
                  busy={busy}
                  onSave={save}
                  onCancel={() => {
                    setCreating(false);
                    setEditing(false);
                    setTemplateValues(undefined);
                  }}
                  onShowToast={showToast}
                />
              </div>
            ) : selected ? (
              <div className="automation-detail-view">
                <div className="automation-detail-header">
                  <div className="automation-detail-title-group">
                    <h2>{selected.name}</h2>
                    <span className={`automation-status-badge automation-status-badge--${selected.status === "ACTIVE" ? "active" : selected.pauseReason === "one_shot_fired" ? "ended" : "paused"}`}>
                      {selected.status === "ACTIVE" ? "已启用" : selected.pauseReason === "one_shot_fired" ? "已结束" : "已暂停"}
                    </span>
                  </div>
                  <div className="automation-detail-actions">
                    {selected.deletedAt ? (
                      <span className="automation-deleted-notice">任务已删除，执行历史仍然保留。</span>
                    ) : (
                      <>
                        <Button variant="secondary" size="small" disabled={busy} onClick={() => setEditing(true)}>
                          编辑
                        </Button>
                        <Button variant="secondary" size="small" disabled={busy} onClick={() => void mutate("update")}>
                          {selected.status === "ACTIVE" ? "暂停" : "恢复"}
                        </Button>
                        <Button variant="primary" size="small" disabled={busy || selected.status !== "ACTIVE"} onClick={() => void runNow()}>
                          立即执行
                        </Button>
                        <Button variant="danger" size="small" disabled={busy} onClick={() => void mutate("delete")}>
                          删除计划
                        </Button>
                      </>
                    )}
                  </div>
                </div>

                {selected.pauseReason && (
                  <p role="status" className="automation-pause-banner">
                    {selected.pauseReason === "one_shot_fired" ? "这个一次性任务已经结束。" : selected.pauseReason}
                  </p>
                )}

                <div className="automation-detail-card">
                  <h3 className="automation-card-title">任务内容</h3>
                  <p className="automation-prompt">{selected.prompt}</p>
                </div>

                <div className="automation-detail-card">
                  <h3 className="automation-card-title">配置信息</h3>
                  <dl className="automation-facts">
                    <dt>时间规则</dt>
                    <dd>{selected.schedule.runAt ? formatAutomationTime(selected.schedule.runAt, selected.schedule.timezone)
                      : `${selected.schedule.expression} · ${selected.schedule.timezone}`}</dd>
                    <dt>执行位置</dt>
                    <dd>
                      {selected.target.kind === "existing_session" ? "已有会话" : "每次新建会话"} ·{" "}
                      {projects.find((project) => project.id === selected.target.projectId)?.name ?? (selected.target.projectId ? "项目不可用" : "无项目")}
                    </dd>
                    <dt>模型</dt>
                    <dd>{models.find((model) => model.id === selected.model.modelId)?.name ?? selected.model.modelId}</dd>
                    <dt>审批模式</dt>
                    <dd>{selected.approvalMode === "full_access" ? "完全访问" : selected.approvalMode === "auto_review" ? "替我审批" : "请求审批"}</dd>
                  </dl>
                </div>

                <div className="automation-detail-card">
                  <h3 className="automation-card-title">执行历史</h3>
                  {!history.length ? (
                    <p className="automation-empty-hint">还没有执行记录。</p>
                  ) : (
                    <ul className="automation-history">
                      {history.map((record) => (
                        <li key={record.id} className="automation-history-item">
                          <div className="automation-history-meta">
                            <span>
                              {record.scheduledFor.startsWith("manual:") ? "手动执行" : formatAutomationTime(record.scheduledFor, record.snapshot.schedule.timezone)} ·{" "}
                              {(record.reconciliationRequired
                                ? "操作结果未知"
                                : record.runStatus
                                ? ({
                                    queued: "等待执行",
                                    running: "执行中",
                                    waiting_input: "等待回答",
                                    waiting_approval: "等待审批",
                                    waiting_agents: "等待子任务",
                                    finalizing: "正在结束",
                                    succeeded: "已完成",
                                    stopped: "已结束",
                                    failed: "执行失败",
                                    canceled: "已取消",
                                    interrupted: "执行已中断",
                                  } as Record<string, string>)[record.runStatus]
                                : null) ??
                                ({
                                  preparing: "准备中",
                                  submitted: "已提交执行",
                                  skipped: "已跳过",
                                  failed: "准备失败",
                                  interrupted: "准备已中断",
                                })[record.status]}
                            </span>
                            {record.reason && <p className="automation-history-reason">{record.reason}</p>}
                          </div>
                          {record.sessionId && (
                            <Button size="small" variant="secondary" onClick={() => onOpenSession(record.sessionId!)}>
                              查看执行结果
                            </Button>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {historyCursor && (
                    <div className="automation-more-wrap">
                      <Button size="small" variant="secondary" onClick={() => void moreHistory()}>
                        加载更多记录
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="automation-hero">
                <div className="automation-hero-center">
                  <ClockGraphic />
                  <h1 className="automation-hero-title">安排任务</h1>
                  <p className="automation-hero-subtitle">Eidos 可以帮你处理持续性任务，让你不必亲自动手。</p>

                  <div className="automation-templates-grid">
                    {TEMPLATES.map((tmpl) => (
                      <button
                        key={tmpl.id}
                        type="button"
                        className="automation-template-card"
                        onClick={() => handleApplyTemplate(tmpl)}
                      >
                        <div className="automation-template-icon" aria-hidden="true">
                          {tmpl.icon}
                        </div>
                        <div className="automation-template-content">
                          <strong className="automation-template-title">{tmpl.title}</strong>
                          <p className="automation-template-desc">{tmpl.desc}</p>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
