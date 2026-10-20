import { useEffect, useRef, useState } from "react";
import type { FindOptions } from "./find-text.js";
import { CloseIcon } from "./MenuIcons.js";

export function SearchIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 4.5 4.5" /></svg>;
}

export function FindBar({ query, onQuery, onClose, onNext, onPrevious, status, options, onOptions, placeholder = "搜索聊天…" }: {
  query: string; onQuery: (query: string) => void; onClose: () => void;
  onNext: () => void; onPrevious: () => void; status: string;
  options?: FindOptions; onOptions?: (options: FindOptions) => void; placeholder?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const [draft, setDraft] = useState(query);
  useEffect(() => { input.current?.focus(); input.current?.select(); }, []);
  useEffect(() => setDraft(query), [query]);
  return <div className={`find-bar${options ? " find-bar--file" : ""}`} role="search" aria-label={placeholder}>
    <div className="find-bar-input">{!options && <SearchIcon />}<input ref={input} aria-label={placeholder} placeholder={placeholder} value={draft} maxLength={256}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={(event) => { composing.current = false; onQuery(event.currentTarget.value); }}
      onChange={(event) => { setDraft(event.target.value); if (!composing.current) onQuery(event.target.value); }}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || composing.current) return;
        if (event.key === "Escape") { event.stopPropagation(); onClose(); }
        if (event.key === "Enter") { event.preventDefault(); event.shiftKey ? onPrevious() : onNext(); }
      }} />
      {options && ([ ["caseSensitive", "Aa", "区分大小写"], ["wholeWord", "ab", "全字匹配"], ["regex", ".*", "正则表达式"] ] as const).map(([key, label, title]) =>
        <button key={key} aria-label={title} title={title} aria-pressed={options[key]} onClick={() => onOptions?.({ ...options, [key]: !options[key] })}>{label}</button>)}</div>
    <span className="find-bar-status" role="status" title={status}>{status}</span>
    <button aria-label="上一处" onClick={onPrevious}>↑</button><button aria-label="下一处" onClick={onNext}>↓</button>
    <button className="find-bar-close" aria-label="关闭查找" onClick={onClose}><CloseIcon /></button>
  </div>;
}
