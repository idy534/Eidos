import { useEffect, useMemo, useRef, useState } from "react";
import type { Project, Session } from "../contracts.js";
import type { SearchHit, SearchResponse } from "../../../shared/search.js";
import { userFacingError } from "../session-state.js";
import { FindBar, SearchIcon } from "./FindBar.js";
import { DEFAULT_FIND_OPTIONS, findText } from "./find-text.js";
import { ToastContainer } from "./ToastContainer.js";
import { CloseIcon } from "./MenuIcons.js";
import { FolderIcon } from "./InputPicker.js";
import { formatSessionTime } from "./SidebarTooltip.js";

function SearchSnippet({ text, query }: { text: string; query: string }) {
  const matches = findText(text, query, DEFAULT_FIND_OPTIONS).matches;
  let cursor = 0;
  const parts = matches.flatMap((match, index) => {
    const before = text.slice(cursor, match.start); cursor = match.end;
    return [before, <mark key={index}>{text.slice(match.start, match.end)}</mark>];
  });
  return <>{parts}{text.slice(cursor)}</>;
}

export function ChatSearch({ mode, sessionId, projects, sessions = [], onClose, onSelect, onClear }: {
  mode: "global" | "session"; sessionId?: string; projects: Project[]; sessions?: Session[];
  onClose: () => void; onSelect: (hit: SearchHit, query: string) => Promise<void>;
  onClear?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [scope, setScope] = useState("");
  const [response, setResponse] = useState<SearchResponse>();
  const [cursor, setCursor] = useState<string>();
  const [previous, setPrevious] = useState<(string | undefined)[]>([]);
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [now, setNow] = useState(Date.now);
  const recent = mode === "global" && !query.trim();
  const recentSessions = useMemo(() => recent ? sessions.filter((session) =>
    (session.taskStatus !== "new" || Boolean(session.title?.trim()) || Boolean(session.forkedFrom))
    && (scope === "projectless" ? !session.project : !scope || session.project?.id === scope))
    .sort((a, b) => (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt) || b.createdAt - a.createdAt || a.id.localeCompare(b.id))
    .slice(0, 10) : [], [recent, sessions, scope]);
  const recentTimes = useMemo(() => recentSessions.map((session) => formatSessionTime(session.updatedAt || session.createdAt)), [recentSessions, now]);
  const displayedHits: SearchHit[] = recent ? recentSessions.map((session) => ({
    sessionId: session.id, title: session.title?.trim() || "新会话", itemId: null, snippet: "", matchKind: "title", occurrence: 0,
    projectName: session.project ? projects.find((project) => project.id === session.project?.id)?.name?.trim() || session.project.name?.trim() || session.project.workspaceRoot.split("/").filter(Boolean).at(-1) || "项目" : "",
  })) : response?.items ?? [];
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const results = useRef<HTMLDivElement>(null);
  const composing = useRef(false);
  const selection = useRef("");
  const lastRequest = useRef("");
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  useEffect(() => {
    results.current?.querySelector<HTMLElement>(".chat-search-result.is-selected")?.scrollIntoView?.({ block: "nearest" });
  }, [selected, query, scope]);
  useEffect(() => {
    if (!recent) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, [recent]);
  useEffect(() => {
    const trigger = document.activeElement;
    if (mode === "global") { dialog.current?.showModal(); input.current?.focus(); }
    return () => { if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus({ preventScroll: true }); };
  }, [mode]);
  useEffect(() => {
    setResponse(undefined); setSelected(0); setError("");
  }, [query, scope, mode, sessionId]);
  useEffect(() => {
    if (!query.trim()) { setBusy(false); return; }
    const requestKey = JSON.stringify([query, scope, mode, sessionId, cursor]);
    const foreground = lastRequest.current !== requestKey;
    lastRequest.current = requestKey;
    let current = true;
    let retry: ReturnType<typeof setTimeout>;
    const timer = setTimeout(() => {
      setBusy(true);
      void window.eidosRuntime.searchSessions({ query, ...(cursor ? { cursor } : {}),
        ...(mode === "session" && sessionId ? { sessionId } : {}),
        ...(scope === "projectless" ? { projectless: true } : scope ? { projectId: scope } : {}) }).then((next) => {
        if (!current) return;
        setResponse(next); setBusy(false);
        setSelected((index) => foreground ? 0 : Math.max(0, Math.min(index, next.items.length - 1)));
        if (next.reset) { setPrevious([]); setCursor(undefined); }
        if (mode === "session" && next.items[0] && (foreground || !selection.current)) {
          const key = `${query}:${next.items[0].itemId}:${next.items[0].occurrence}`;
          if (selection.current !== key) { selection.current = key; void selectRef.current(next.items[0], query).catch((cause) => { if (current) setError(userFacingError(cause)); }); }
        }
        if (next.indexing) retry = setTimeout(() => { if (current) setRefresh((n) => n + 1); }, 350);
      }).catch((cause) => { if (current) { setBusy(false); setError(userFacingError(cause)); } });
    }, 200);
    return () => { current = false; clearTimeout(timer); clearTimeout(retry); };
  }, [query, scope, mode, sessionId, cursor, refresh]);
  const changeQuery = (value: string) => { onClear?.(); setDraft(value); setQuery(value); setCursor(undefined); setPrevious([]); selection.current = ""; };
  const select = (index: number) => {
    const hit = displayedHits[index];
    if (!hit) return;
    if (recent) onClear?.();
    setSelected(index);
    void onSelect(hit, query).catch((cause) => setError(userFacingError(cause)));
  };
  const next = () => {
    if (response && selected + 1 < response.items.length) select(selected + 1);
    else if (response?.nextCursor) { setPrevious((p) => [...p, cursor]); setCursor(response.nextCursor); }
    else if (response?.items.length) select(0);
  };
  const back = () => {
    if (selected > 0) select(selected - 1);
    else if (previous.length) { setCursor(previous.at(-1)); setPrevious((p) => p.slice(0, -1)); }
    else if (response?.items.length) select(response.items.length - 1);
  };
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "g" && mode === "session") { event.preventDefault(); event.shiftKey ? back() : next(); }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  });
  if (mode === "session") return <FindBar query={query} onQuery={changeQuery} onClose={onClose} onNext={next} onPrevious={back}
    status={error || (busy && !response ? "正在搜索…" : `${response?.items.length ? selected + 1 : 0}/${response?.items.length ?? 0}${response?.nextCursor || response?.indexing ? "+" : ""}`)} />;
  return <dialog ref={dialog} className="chat-search-dialog" aria-label="搜索所有会话" onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === dialog.current) onClose(); }}>
    <div className="chat-search-input"><SearchIcon /><input ref={input} value={draft} placeholder="搜索所有会话…" aria-label="搜索所有会话" maxLength={256}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={(event) => { composing.current = false; changeQuery(event.currentTarget.value); }}
      onChange={(event) => { if (composing.current) setDraft(event.target.value); else changeQuery(event.target.value); }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "ArrowDown") { event.preventDefault(); setSelected((index) => Math.min(index + 1, Math.max(0, displayedHits.length - 1))); }
        if (event.key === "ArrowUp") { event.preventDefault(); setSelected((index) => Math.max(0, index - 1)); }
        if (event.key === "Enter") { event.preventDefault(); select(selected); }
      }} /><select className="chat-search-scope" aria-label="搜索范围" value={scope} onChange={(event) => { setScope(event.target.value); setCursor(undefined); setPrevious([]); }}>
      <option value="">所有项目</option><option value="projectless">无项目</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name || project.workspaceRoot.split("/").at(-1)}</option>)}
    </select><button className="chat-search-close-btn" aria-label="关闭搜索" onClick={onClose}><CloseIcon /></button></div>
    <div ref={results} className={`chat-search-results${recent ? " chat-search-results--recent" : ""}`} aria-busy={busy}>
      {error && (
        <ToastContainer
          toasts={[{
            id: `chat-search-error-${error}`,
            message: error,
            type: "error",
            duration: 4000,
          }]}
          onDismiss={() => setError("")}
        />
      )}
      {recent && !recentSessions.length && (
        <div className="chat-search-empty-state">
          <div className="chat-search-empty-icon"><SearchIcon /></div>
          <p className="chat-search-empty">输入关键词，查找所有项目中的聊天记录</p>
        </div>
      )}
      {recent && displayedHits.map((hit, index) => {
        const activity = recentSessions[index]!.updatedAt || recentSessions[index]!.createdAt;
        return <button key={hit.sessionId} className={`chat-search-result chat-search-result--recent${selected === index ? " is-selected" : ""}`} onClick={() => select(index)}>
          <span className="chat-search-recent-main"><span className="chat-search-result-title">{hit.title}</span>
            {hit.projectName && <span className="chat-search-recent-project"><FolderIcon /><span>{hit.projectName}</span></span>}
          </span>
          <time className="chat-search-recent-time" dateTime={new Date(activity).toISOString()} title={recentTimes[index]!.exact}>{recentTimes[index]!.friendly}</time>
        </button>;
      })}
      {!recent && busy && !response && <p role="status" className="chat-search-status">正在搜索…</p>}
      {!recent && response?.items.map((hit, index) => <button key={`${hit.sessionId}:${hit.itemId ?? "title"}`} className={`chat-search-result${selected === index ? " is-selected" : ""}`} onClick={() => select(index)}>
        <span className="chat-search-result-title"><SearchSnippet text={hit.title} query={query} /></span><span className="chat-search-result-project">{hit.projectName ? `${hit.projectName} · ` : ""}{hit.matchKind === "title" ? "标题" : "正文"}</span><span className="chat-search-result-snippet"><SearchSnippet text={hit.snippet} query={query} /></span>
      </button>)}
      {!recent && Boolean(response?.projects?.length) && <div className="chat-search-projects">
        <span className="chat-search-projects-label">项目</span>
        <div className="chat-search-projects-list">{response?.projects?.map((project) => <button key={project.id} onClick={() => { setScope(project.id); changeQuery(""); }}>{project.name}</button>)}</div>
      </div>}
      {!recent && response && !response.indexing && !response.items.length && !busy && (
        <div className="chat-search-empty-state">
          <div className="chat-search-empty-icon"><SearchIcon /></div>
          <p className="chat-search-empty">没有找到匹配的会话</p>
        </div>
      )}
      {!recent && (response?.nextCursor || previous.length > 0) && (
        <div className="chat-search-pagination">
          {previous.length > 0 && <button className="chat-search-more" onClick={() => { setCursor(previous.at(-1)); setPrevious((p) => p.slice(0, -1)); }}>上一页</button>}
          {response?.nextCursor && <button className="chat-search-more" onClick={() => { setPrevious((p) => [...p, cursor]); setCursor(response.nextCursor ?? undefined); }}>下一页</button>}
        </div>
      )}
    </div>
  </dialog>;
}
