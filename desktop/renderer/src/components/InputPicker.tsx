import { useEffect, useId, useRef, useState } from "react";
import type { InputPrepareRequest } from "../../../shared/input-context.js";
import { useInputContext } from "./InputContext.js";
import { userFacingError } from "../session-state.js";

export type InputPickerMode = "all" | "skill" | "commands";
interface Candidate { id: string; label: string; detail: string; request?: InputPrepareRequest; action?: string; unavailable?: boolean }
const commands: Candidate[] = [
  { id: "model", label: "/model", detail: "选择模型", action: "model" },
  { id: "skills", label: "/skills", detail: "选择 Skill", action: "skills" },
  { id: "mcp", label: "/mcp", detail: "查看与管理 MCP", action: "mcp" },
  { id: "plugins", label: "/plugins", detail: "管理 Plugin", action: "plugins" },
  { id: "status", label: "/status", detail: "查看当前上下文", action: "status" },
];
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
      if (skills.status === "fulfilled") for (const value of skills.value.skills) rows.push({
        id: `skill:${value.qualifiedId}`, label: value.name, detail: `Skill · ${value.qualifiedId}${!value.available || !value.enabled ? " · 不可用，请前往设置" : ""}`,
        request: { kind: "skill", source: value.qualifiedId }, unavailable: !value.available || !value.enabled,
      });
      if (plugins.status === "fulfilled") for (const value of plugins.value.plugins) rows.push({
        id: `plugin:${value.id}`, label: value.name, detail: `Plugin · ${value.id}${!value.enabled ? " · 未启用" : ""}`,
        request: { kind: "plugin", source: value.id }, unavailable: !value.enabled || value.status !== "installed",
      });
      if (mcp.status === "fulfilled") for (const value of mcp.value.servers) rows.push({
        id: `mcp:${value.pluginId}:${value.serverId}`, label: value.serverId,
        detail: `MCP · ${value.pluginId}${!value.available || !value.consented ? " · 未授权或不可用" : ""}`,
        request: { kind: "mcp", source: value.serverId, pluginId: value.pluginId }, unavailable: !value.available || !value.consented,
      });
      if (sessions.status === "fulfilled") for (const value of sessions.value.items) rows.push({
        id: `history:${value.id}`, label: value.title || "未命名对话", detail: `历史对话 · ${value.workspaceRoot}`,
        request: { kind: "history", source: value.id },
      });
      if ([skills, plugins, mcp, sessions].some((value) => value.status === "rejected")) setError("部分候选未能加载，请关闭后重试。");
      setCatalog(rows); setLoading(false);
    });
    return () => { active = false; };
  }, [mode, context?.sessionId]);

  useEffect(() => {
    let active = true;
    if (mode !== "all" || !context || context.sessionId.startsWith("draft-")) { setFiles([]); return; }
    const slash = query.lastIndexOf("/");
    const directory = slash < 0 ? "." : query.slice(0, slash) || ".";
    if (directory.startsWith("/") || directory.split("/").includes("..")) { setFiles([]); return; }
    const timer = setTimeout(() => {
      void window.eidosRuntime.listWorkspaceDirectory(context.sessionId, directory, 200).then((result) => {
        if (!active) return;
        setFiles(result.entries.map((entry) => ({ id: `file:${entry.relativePath}`, label: entry.name,
          detail: `${entry.kind === "directory" ? "文件夹" : "文件"} · ${entry.relativePath}`,
          request: { kind: entry.kind === "directory" ? "directory" : "file", source: `${context.workspaceRoot}/${entry.relativePath}` },
        })));
        if (result.truncated) setError("当前目录结果较多，请输入子目录路径缩小范围。");
      }, (cause) => { if (active) { setFiles([]); setError(userFacingError(cause)); } });
    }, 150);
    return () => { active = false; clearTimeout(timer); };
  }, [mode, query, context?.sessionId, context?.workspaceRoot]);

  const needle = query.toLocaleLowerCase();
  const candidates = (history ?? [...files, ...catalog]).filter((value) =>
    (mode !== "skill" || value.request?.kind === "skill")
    && (category === "all" || value.request?.kind === category || (category === "file" && ["file", "directory"].includes(value.request?.kind ?? "")))
    && `${value.label} ${value.detail}`.toLocaleLowerCase().includes(needle),
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
  return <div className="input-picker" ref={root}>
    <div className="input-picker-toolbar">
      {!inline && <input autoFocus aria-label="搜索引用" placeholder="搜索名称，或输入 src/ 查看目录" value={query} onChange={(event) => onQuery(event.target.value)} />}
      <button type="button" aria-label="关闭引用选择" onClick={onClose}>×</button>
    </div>
    {mode === "all" && !inline && <>
      <div className="input-picker-actions">
        <button type="button" onClick={() => { void window.eidosRuntime.pickInputPaths().then((paths) => context?.paths(paths)).catch((cause) => setError(userFacingError(cause))); }}>添加文件 / 图片</button>
        <button type="button" onClick={() => { void window.eidosRuntime.pickInputPaths(true).then((paths) => context?.paths(paths)).catch((cause) => setError(userFacingError(cause))); }}>添加文件夹</button>
        <button type="button" onClick={() => { context?.settings("plugins"); onClose(); }}>管理扩展</button>
      </div>
      <select aria-label="引用类型" value={category} onChange={(event) => { setCategory(event.target.value); setHistory(null); }}>
        {[["all", "全部"], ["file", "文件与文件夹"], ["skill", "Skill"], ["mcp", "MCP"], ["plugin", "Plugin"], ["history", "历史对话"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </>}
    {loading && <p role="status">正在读取候选…</p>}
    {error && <p role="alert">{error}</p>}
    {history && <button type="button" onClick={() => setHistory(null)}>返回历史对话</button>}
    <div id={listId} role="listbox" aria-label="引用候选" className="input-picker-list">
      {candidates.map((candidate, row) => <div key={candidate.id}>
        <button type="button" id={`${listId}-${row}`} role="option" aria-selected={index === row} aria-disabled={candidate.unavailable}
          className={index === row ? "is-selected" : ""} onPointerMove={() => setIndex(row)} onMouseDown={(event) => event.preventDefault()}
          onClick={() => choose(candidate)}>
          <span>{candidate.label}</span><small>{candidate.detail}</small>
        </button>
        {candidate.request?.kind === "history" && !candidate.request.itemIds && <button type="button" className="input-picker-history" onClick={() => {
          const source = candidate.request!.source;
          void window.eidosRuntime.readSession(source, { itemLimit: 100 }).then((snapshot) => {
            setCategory("all"); onQuery("");
            setHistory([{ ...candidate, label: "引用最近 20 条消息" }, ...snapshot.items.filter((item) => ["user_message", "assistant_message"].includes(item.kind) && item.content).map((item) => ({
              id: item.id, label: (item.content ?? "").slice(0, 100), detail: `${item.kind === "user_message" ? "用户" : "助手"} · ${item.id}`,
              request: { kind: "history" as const, source, itemIds: [item.id], label: `${candidate.label} · 消息摘录` },
            }))]);
          }, (cause) => setError(userFacingError(cause)));
        }}>选择消息…</button>}
      </div>)}
    </div>
    {!loading && candidates.length === 0 && <p>没有匹配项。文件也可以通过“添加文件”或拖放选择。</p>}
  </div>;
}
