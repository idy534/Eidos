import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Automation, AutomationOccurrence, AutomationRequest, AutomationSummary } from "../../../../shared/automation.js";
import type { ModelOption, Project, Session } from "../../contracts.js";
import { Button } from "../Button.js";
import { AutomationForm, type InitialAutomationValues } from "./AutomationForm.js";
import { userFacingError } from "../../session-state.js";

interface Props {
  projects: Project[];
  sessions: Session[];
  models: ModelOption[];
  currentSessionId?: string | undefined;
  currentProjectId?: string | undefined;
  runtimeVersion?: string | undefined;
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
    id: "agent-memory",
    icon: "🤖",
    title: "Agent 长期记忆研究速览",
    desc: "每周五筛选影响桌面 Agent 记忆架构的新论文与工程实践",
    prompt: "筛选本周影响桌面 Agent 记忆架构、会话持久化和经验检索的新论文与工程实践，输出重点速览与选型建议。",
    scheduleKind: "cron",
    expression: "0 9 * * 5",
  },
  {
    id: "grok-pricing",
    icon: "💻",
    title: "Grok Coding Plan 订阅变动监控",
    desc: "每周检查 Grok Coding Plan 的价格、使用额度和模型权益，只提醒影响使用成本或开...",
    prompt: "检查 Grok Coding Plan 的价格、使用额度和模型权益，对比上周变动，只提醒影响使用成本或开发效率的关键变化。",
    scheduleKind: "cron",
    expression: "0 9 * * 1",
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
    id: "financial-update",
    icon: "💰",
    title: "每周财务状况更新",
    desc: "回顾我最近的支出，看看有什么需要注意。",
    prompt: "回顾我最近一周的支出账单与订阅扣费，分析消费构成，看看有什么需要注意的异常或超支项。",
    scheduleKind: "cron",
    expression: "0 20 * * 0",
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

function FilterIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true">
      <path d="M2.5 4h11" />
      <path d="M4.5 8h7" />
      <path d="M6.5 12h3" />
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

function formatScheduleSummary(task: AutomationSummary): string {
  let datePart = "";
  if (task.nextRunAt) {
    const d = new Date(task.nextRunAt);
    const month = d.getMonth() + 1;
    const date = d.getDate();
    datePart = `${month}月${date}日`;
  } else if (task.status === "PAUSED") {
    datePart = "已暂停";
  } else if (task.pauseReason === "one_shot_fired") {
    datePart = "已结束";
  } else {
    datePart = "暂无执行";
  }

  let freqPart = "";
  if (task.kind === "cron" && task.schedule?.expression) {
    const expr = task.schedule.expression.trim();
    if (expr === "0 9 * * *") freqPart = "每天";
    else if (expr.endsWith("* * 1-5") || expr.endsWith("* * 1,2,3,4,5")) freqPart = "工作日";
    else if (expr.includes("* * 0") || expr.includes("* * 1") || expr.includes("* * 5")) freqPart = "每周";
    else if (expr.startsWith("*/10")) freqPart = "每10分钟";
    else if (expr.startsWith("*/30")) freqPart = "每30分钟";
    else if (expr.startsWith("0 *")) freqPart = "每小时";
    else freqPart = "周期";
  } else if (task.kind === "at") {
    freqPart = "一次性";
  } else {
    freqPart = "定时";
  }

  return `${datePart} · ${freqPart}`;
}

export function AutomationPage({
  projects,
  sessions,
  models,
  currentSessionId,
  currentProjectId,
  runtimeVersion,
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
  const [error, setError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  const [filterMode, setFilterMode] = useState<"all" | "active" | "paused">("active");

  const selection = useRef(0);
  const listVersion = useRef(0);
  const mounted = useRef(true);

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
      if (mounted.current && version === listVersion.current) setError(userFacingError(failure));
    } finally {
      if (mounted.current && version === listVersion.current) setLoading(false);
    }
  }, []);

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
            if (!disposed) setError(userFacingError(failure));
          });
      }, 50);
    });
    return () => {
      disposed = true;
      clearTimeout(timer);
      unsubscribe();
    };
  }, [selected?.id, editing]);

  const read = async (id: string) => {
    const version = ++selection.current;
    setError("");
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
      if (version === selection.current) setError(userFacingError(failure));
    } finally {
      if (version === selection.current) setBusy(false);
    }
  };

  const save = async (command: AutomationRequest) => {
    setBusy(true);
    setError("");
    try {
      const result = await window.eidosRuntime.automation({
        operationId: crypto.randomUUID(),
        command,
        ...(currentSessionId ? { contextSessionId: currentSessionId } : {}),
      });
      setSelected(result.automation ?? null);
      setCreating(false);
      setEditing(false);
      setTemplateValues(undefined);
      await refresh();
    } catch (failure) {
      setError(userFacingError(failure));
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
    setError("");
    try {
      await window.eidosRuntime.automationRunNow({ id: selected.id, operationId: crypto.randomUUID() });
      await read(selected.id);
    } catch (failure) {
      setError(userFacingError(failure));
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
      setError(userFacingError(failure));
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

  const filteredItems = useMemo(() => {
    let list = items;
    if (filterMode === "active") {
      list = list.filter((t) => t.status === "ACTIVE");
    } else if (filterMode === "paused") {
      list = list.filter((t) => t.status === "PAUSED" || t.pauseReason === "one_shot_fired");
    }
    if (searchQuery.trim()) {
      const query = searchQuery.trim().toLowerCase();
      list = list.filter((t) => t.name.toLowerCase().includes(query));
    }
    return list;
  }, [items, filterMode, searchQuery]);

  const handleToggleFilter = () => {
    setFilterMode((curr) => {
      if (curr === "active") return "all";
      if (curr === "all") return "paused";
      return "active";
    });
  };

  return (
    <div className="automation-page-wrapper">
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

            <div className="automation-section-header">
              <span className="automation-section-title">
                {filterMode === "active" ? "即将执行" : filterMode === "paused" ? "已暂停" : "全部任务"}
              </span>
              <button
                type="button"
                className="automation-icon-btn"
                aria-label="筛选任务"
                title={`当前筛选: ${filterMode === "active" ? "即将执行" : filterMode === "paused" ? "已暂停" : "全部任务"}，点击切换`}
                onClick={handleToggleFilter}
              >
                <FilterIcon />
              </button>
            </div>

            <div className="automation-list-scroll">
              {loading ? (
                <p className="automation-status-text" role="status">正在读取任务…</p>
              ) : !items.length ? (
                <p className="automation-empty-hint">还没有定时任务。你可以在这里创建，也可以在对话中提出要求。</p>
              ) : !filteredItems.length ? (
                <p className="automation-empty-hint">无匹配的定时任务。</p>
              ) : (
                filteredItems.map((task) => {
                  const isSelected = selected?.id === task.id && !creating;
                  return (
                    <button
                      type="button"
                      disabled={busy}
                      className={`automation-task-item${isSelected ? " selected" : ""}`}
                      key={task.id}
                      onClick={() => void read(task.id)}
                    >
                      <div className="automation-task-item-main">
                        <strong className="automation-task-name">{task.name}</strong>
                        <span className="automation-task-schedule">{formatScheduleSummary(task)}</span>
                      </div>
                      {task.status === "ACTIVE" && <span className="automation-active-dot" aria-hidden="true" />}
                    </button>
                  );
                })
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
            {error && (
              <div className="approval-error" role="alert">
                {error} <Button size="small" onClick={() => void refresh()}>刷新列表</Button>
              </div>
            )}

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
                    <dd>{selected.schedule.expression ?? selected.schedule.runAt} · {selected.schedule.timezone}</dd>
                    <dt>执行位置</dt>
                    <dd>
                      {selected.target.kind === "existing_session" ? "已有会话" : "每次新建会话"} ·{" "}
                      {projects.find((project) => project.id === selected.target.projectId)?.name ?? (selected.target.projectId ? "项目不可用" : "无项目")}
                    </dd>
                    <dt>模型</dt>
                    <dd>{models.find((model) => model.id === selected.model.modelId)?.name ?? selected.model.modelId}</dd>
                    <dt>审批模式</dt>
                    <dd>{selected.approvalMode === "auto_review" ? "替我审批" : "请求审批"}</dd>
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
                              {record.scheduledFor.startsWith("manual:") ? "手动执行" : new Date(record.scheduledFor).toLocaleString()} ·{" "}
                              {(record.reconciliationRequired
                                ? "操作结果待核验"
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
