import { useEffect, useId, useRef, useState } from "react";
import type { InputPrepareRequest } from "../../../shared/input-context.js";
import { useInputContext } from "./InputContext.js";
import { userFacingError } from "../session-state.js";

export type InputPickerMode = "all" | "skill" | "commands";
interface Candidate { id: string; label: string; detail: string; request?: InputPrepareRequest; action?: string; unavailable?: boolean }
const commands: Candidate[] = [
  { id: "skills", label: "/skills", detail: "选择 Skill", action: "skills" },
  { id: "mcp", label: "/mcp", detail: "查看与管理 MCP", action: "mcp" },
  { id: "plugins", label: "/plugins", detail: "管理 Plugin", action: "plugins" },
];
const CATEGORIES = [
  { id: "all", label: "全部" },
  { id: "file", label: "文件与目录" },
  { id: "skill", label: "Skill" },
  { id: "mcp", label: "MCP" },
  { id: "plugin", label: "Plugin" },
  { id: "history", label: "历史对话" },
] as const;
export function InputPicker({ mode, query, inline, onQuery, onChoose, onClose, onCommand, onListId }: {
  mode: InputPickerMode; query: string; inline: boolean;
  onQuery(value: string): void; onChoose(request: InputPrepareRequest): void; onClose(): void;
  onCommand(command: string): void; onListId?(id: string, active: string | undefined): void;
}) {
  const context = useInputContext();
  const [catalog, setCatalog] = useState<Candidate[]>([]);
  const [files, setFiles] = useState<Candidate[]>([]);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [index, setIndex] = useState(0);
  const [category, setCategory] = useState("all");
  const [history, setHistory] = useState<Candidate[] | null>(null);
  const listId = useId();
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    setLoading(true);
    if (mode === "commands") { setCatalog(commands); setLoading(false); return; }
    void Promise.allSettled([
      window.eidosRuntime.listSkills(), window.eidosRuntime.listPlugins(),
      window.eidosRuntime.listMcpServers(), window.eidosRuntime.listSessions(),
    ]).then(([skills, plugins, mcp, sessions]) => {
      if (!active) return;
      const rows: Candidate[] = [];
      if (skills.status === "fulfilled") for (const value of skills.value.skills) {
        const source = value.sourceKind === "system" ? "系统" : value.sourceKind === "user" ? "个人" : value.pluginId || "插件";
        rows.push({
          id: `skill:${value.qualifiedId}`, label: value.name, detail: `skill · ${source}`,
          request: { kind: "skill", source: value.qualifiedId }, unavailable: !value.available || !value.enabled,
        });
      }
      if (plugins.status === "fulfilled") for (const value of plugins.value.plugins) rows.push({
        id: `plugin:${value.id}`, label: value.name, detail: `plugin · ${value.id}`,
        request: { kind: "plugin", source: value.id }, unavailable: !value.enabled || value.status !== "installed",
      });
      if (mcp.status === "fulfilled") for (const value of mcp.value.servers) rows.push({
        id: `mcp:${value.pluginId}:${value.serverId}`, label: value.serverId,
        detail: `mcp · ${value.pluginId}`,
        request: { kind: "mcp", source: value.serverId, pluginId: value.pluginId }, unavailable: !value.available || !value.consented,
      });
      if (sessions.status === "fulfilled") for (const value of sessions.value.items) {
        const isProjectless = Boolean(value.projectless || !value.project);
        const projectName = !isProjectless ? (value.project?.name || value.project?.workspaceRoot.split("/").filter(Boolean).at(-1)) : undefined;
        const detail = projectName ? `历史对话 · ${projectName}` : "历史对话";
        rows.push({
          id: `history:${value.id}`, label: value.title || "未命名对话", detail,
          request: { kind: "history", source: value.id },
        });
      }
      if ([skills, plugins, mcp, sessions].some((value) => value.status === "rejected")) setError("部分候选未能加载，请关闭后重试。");
      setCatalog(rows); setLoading(false);
    });
    return () => { active = false; };
  }, [mode, context?.sessionId]);

  useEffect(() => {
    let active = true;
    if (mode !== "all" || !context || context.sessionId.startsWith("draft-")) { setFiles([]); return; }
    void window.eidosRuntime.listWorkspaceDirectory(context.sessionId, ".", 200).then((result) => {
      if (!active) return;
      setFiles(result.entries.map((entry) => ({ id: `file:${entry.relativePath}`, label: entry.name,
        detail: entry.kind === "directory" ? "文件夹" : "文件",
        request: { kind: entry.kind === "directory" ? "directory" : "file", source: `${context.workspaceRoot}/${entry.relativePath}` },
      })));
      if (result.truncated) setError("当前目录结果较多。");
    }, (cause) => { if (active) { setFiles([]); setError(userFacingError(cause)); } });
    return () => { active = false; };
  }, [mode, context?.sessionId, context?.workspaceRoot]);

  const needle = query.toLocaleLowerCase();
  const candidates = (history ?? [...files, ...catalog]).filter((value) =>
    (mode !== "skill" || value.request?.kind === "skill")
    && (category === "all" || value.request?.kind === category || (category === "file" && ["file", "directory"].includes(value.request?.kind ?? "")))
    && `${value.label} ${value.detail} ${value.request?.source ?? ""}`.toLocaleLowerCase().includes(needle),
  ).slice(0, 80);
  const choose = (candidate: Candidate | undefined) => {
    if (!candidate || candidate.unavailable) return;
    if (candidate.action) onCommand(candidate.action);
    else if (candidate.request) onChoose(candidate.request);
  };
  useEffect(() => { setIndex(0); }, [query, category, mode, history]);
  useEffect(() => { onListId?.(listId, candidates[index] ? `${listId}-${index}` : undefined); }, [listId, index, candidates.length, onListId]);
  useEffect(() => { document.getElementById(`${listId}-${index}`)?.scrollIntoView({ block: "nearest" }); }, [index, listId]);
  const choices = useRef({ candidates, choose });
  choices.current = { candidates, choose };
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if (event.isComposing) return;
      if (inline && !(event.target instanceof HTMLTextAreaElement)) return;
      if (!inline && (!root.current?.contains(event.target as Node) || (!(event.target instanceof HTMLInputElement) && (event.target as HTMLElement).getAttribute("role") !== "option"))) return;
      if (["ArrowDown", "ArrowUp", "Enter", "Escape"].includes(event.key)) {
        event.preventDefault(); event.stopPropagation();
        if (event.key === "Escape") onClose();
        else if (event.key === "Enter") choices.current.choose(choices.current.candidates[index]);
        else setIndex((current) => Math.max(0, Math.min(choices.current.candidates.length - 1, current + (event.key === "ArrowDown" ? 1 : -1))));
      }
    }
    document.addEventListener("keydown", key, true);
    return () => document.removeEventListener("keydown", key, true);
  }, [inline, index, onClose]);
  useEffect(() => {
    function outside(event: PointerEvent) {
      const target = event.target as HTMLElement;
      if (!root.current?.contains(target) && !target.closest(".composer")) onClose();
    }
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [onClose]);
  return (
    <div className="input-picker" ref={root}>
      <div className="input-picker__header">
        {!inline ? (
          <div className="input-picker__search">
            <SearchIcon />
            <input
              autoFocus
              aria-label="搜索引用"
              placeholder="搜索名称…"
              value={query}
              onChange={(event) => onQuery(event.target.value)}
            />
            {query && (
              <button
                type="button"
                className="input-picker__clear-btn"
                onClick={() => onQuery("")}
                aria-label="清空搜索"
                tabIndex={-1}
              >
                <ClearIcon />
              </button>
            )}
          </div>
        ) : (
          <div className="input-picker__inline-header">
            <span className="input-picker__inline-title">
              {mode === "skill" ? "选择 Skill" : mode === "commands" ? "快捷指令" : "选择引用"}
            </span>
          </div>
        )}
      </div>

      {mode === "all" && !inline && (
        <>
          <div className="input-picker__quick-actions">
            <button
              type="button"
              className="input-picker__quick-btn"
              onClick={() => {
                void window.eidosRuntime.pickInputPaths().then((paths) => context?.paths(paths)).catch((cause) => setError(userFacingError(cause)));
              }}
            >
              <UploadFileIcon />
              <span>添加文件</span>
            </button>
            <button
              type="button"
              className="input-picker__quick-btn"
              onClick={() => {
                void window.eidosRuntime.pickInputPaths(true).then((paths) => context?.paths(paths)).catch((cause) => setError(userFacingError(cause)));
              }}
            >
              <FolderPlusIcon />
              <span>添加文件夹</span>
            </button>
            <button
              type="button"
              className="input-picker__quick-btn"
              onClick={() => {
                context?.settings("plugins");
                onClose();
              }}
            >
              <SettingsIcon />
              <span>管理扩展</span>
            </button>
          </div>

          <div className="input-picker__categories" role="tablist" aria-label="引用分类">
            {CATEGORIES.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={category === tab.id}
                className={`input-picker__category-pill${category === tab.id ? " is-active" : ""}`}
                onClick={() => {
                  setCategory(tab.id);
                  setHistory(null);
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </>
      )}

      {history && (
        <div className="input-picker__history-banner">
          <button
            type="button"
            className="input-picker__back-btn"
            onClick={() => setHistory(null)}
          >
            <ChevronLeftIcon />
            <span>返回历史对话列表</span>
          </button>
        </div>
      )}

      {loading && (
        <div className="input-picker__status" role="status">
          <span className="input-picker__spinner" />
          <span>正在读取候选…</span>
        </div>
      )}

      {error && (
        <div className="input-picker__error" role="alert">
          <AlertCircleIcon />
          <span>{error}</span>
        </div>
      )}

      <div id={listId} role="listbox" aria-label="引用候选" className="input-picker__list">
        {candidates.map((candidate, row) => (
          <div
            key={candidate.id}
            className={`input-picker__item-row${index === row ? " is-selected" : ""}`}
            onPointerMove={() => setIndex(row)}
          >
            <button
              type="button"
              id={`${listId}-${row}`}
              role="option"
              aria-selected={index === row}
              aria-disabled={candidate.unavailable}
              className={`input-picker__item${index === row ? " is-selected" : ""}${candidate.unavailable ? " is-disabled" : ""}`}
              onPointerMove={() => setIndex(row)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(candidate)}
            >
              <span className={`input-picker__item-icon input-picker__item-icon--${candidate.action ? "command" : candidate.request?.kind ?? "default"}`}>
                {renderItemIcon(candidate)}
              </span>
              <span className="input-picker__item-label">{candidate.label}</span>
              <span className="input-picker__item-detail">{candidate.detail}</span>
              {candidate.unavailable && (
                <span className="input-picker__badge">不可用</span>
              )}
            </button>
            {candidate.request?.kind === "history" && !candidate.request.itemIds && history === null && (
              <button
                type="button"
                className="input-picker__drilldown-btn"
                title="选择消息"
                aria-label="选择消息"
                onClick={(e) => {
                  e.stopPropagation();
                  const source = candidate.request!.source;
                  void window.eidosRuntime.readSession(source, { itemLimit: 100 }).then((snapshot) => {
                    setCategory("all");
                    onQuery("");
                    setHistory([
                      { ...candidate, label: "引用最近 20 条消息" },
                      ...snapshot.items
                        .filter((item) => ["user_message", "assistant_message"].includes(item.kind) && item.content)
                        .map((item) => ({
                          id: item.id,
                          label: (item.content ?? "").slice(0, 100),
                          detail: item.kind === "user_message" ? "用户" : "Eidos",
                          request: {
                            kind: "history" as const,
                            source,
                            itemIds: [item.id],
                            label: `${candidate.label} · 消息摘录`,
                          },
                        })),
                    ]);
                  }, (cause) => setError(userFacingError(cause)));
                }}
              >
                <ChevronRightIcon />
              </button>
            )}
          </div>
        ))}
        {!loading && candidates.length === 0 && (
          <div className="input-picker__empty">
            <SearchEmptyIcon />
            <p className="input-picker__empty-title">没有匹配项</p>
            <p className="input-picker__empty-desc">可以通过上方按钮添加本地文件，或直接拖拽文件到输入框。</p>
          </div>
        )}
      </div>
    </div>
  );
}

function renderItemIcon(candidate: Candidate) {
  if (candidate.action) return <CommandIcon />;
  switch (candidate.request?.kind) {
    case "file":
      return <FileIcon />;
    case "directory":
      return <FolderIcon />;
    case "skill":
      return <SparklesIcon />;
    case "mcp":
      return <ServerIcon />;
    case "plugin":
      return <PuzzleIcon />;
    case "history":
      return <ChatHistoryIcon />;
    default:
      return <FileIcon />;
  }
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="m10.5 10.5 3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function ClearIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6" fill="currentColor" fillOpacity=".15" />
      <path d="m6 6 4 4m0-4-4 4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function UploadFileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <path d="M9.5 2H4.5C3.7 2 3 2.7 3 3.5v9c0 .8.7 1.5 1.5 1.5h7c.8 0 1.5-.7 1.5-1.5V6.5L9.5 2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9.5 2v4.5H13" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M8 8.5v3.5M6.5 10 8 8.5 9.5 10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FolderPlusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <path d="M2 4.5C2 3.7 2.7 3 3.5 3h2.3c.4 0 .8.2 1 .5l.9 1h4.8c.8 0 1.5.7 1.5 1.5v5.5c0 .8-.7 1.5-1.5 1.5h-9C2.7 13 2 12.3 2 11.5v-7Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M8 7v3.5M6.25 8.75h3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true">
      <path d="M2.5 4.5h5M10.5 4.5h3M2.5 11.5h3M8.5 11.5h5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <circle cx="9" cy="4.5" r="1.5" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="7" cy="11.5" r="1.5" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M3.5 2.5h5.5l3.5 3.5v7.5a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-10a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9 2.5V6h3.5" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export function FolderIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M2 4.5C2 3.7 2.7 3 3.5 3h2.3c.4 0 .8.2 1 .5l.9 1h4.8c.8 0 1.5.7 1.5 1.5v5.5c0 .8-.7 1.5-1.5 1.5h-9C2.7 13 2 12.3 2 11.5v-7Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export function SparklesIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M8 2c.3 2.2 1.8 3.7 4 4-2.2.3-3.7 1.8-4 4-.3-2.2-1.8-3.7-4-4 2.2-.3 3.7-1.8 4-4Z" fill="currentColor" fillOpacity=".2" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
      <path d="M12.5 10.5c.2 1 .8 1.6 1.8 1.8-1 .2-1.6.8-1.8 1.8-.2-1-.8-1.6-1.8-1.8 1-.2 1.6-.8 1.8-1.8Z" fill="currentColor" stroke="currentColor" strokeWidth="1" />
    </svg>
  );
}

export function ServerIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <rect x="2.5" y="3" width="11" height="4" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <rect x="2.5" y="9" width="11" height="4" rx="1" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="5" cy="5" r=".75" fill="currentColor" />
      <circle cx="5" cy="11" r=".75" fill="currentColor" />
    </svg>
  );
}

export function PuzzleIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M6 3.5h1.5a1.5 1.5 0 0 1 3 0H12a1 1 0 0 1 1 1v2.5a1.5 1.5 0 0 1 0 3V12a1 1 0 0 1-1 1H9.5a1.5 1.5 0 0 1-3 0H4a1 1 0 0 1-1-1V9.5a1.5 1.5 0 0 1 0-3V4.5a1 1 0 0 1 1-1h2Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export function ChatHistoryIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M2.5 4.5A1.5 1.5 0 0 1 4 3h8a1.5 1.5 0 0 1 1.5 1.5v6A1.5 1.5 0 0 1 12 12H5.5L3 14V4.5Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M5.5 6.5h5M5.5 9h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  );
}

function CommandIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="m10.5 4-5 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="m6 4 4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronLeftIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path d="m10 4-4 4 4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AlertCircleIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.4" />
      <path d="M8 5v3.5M8 11h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function SearchEmptyIcon() {
  return (
    <svg viewBox="0 0 32 32" width="32" height="32" fill="none" aria-hidden="true">
      <circle cx="14" cy="14" r="8" stroke="currentColor" strokeWidth="2" strokeDasharray="3 3" />
      <path d="m20 20 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
