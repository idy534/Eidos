import { useEffect, useRef, useState, type RefObject } from "react";
import { FindBar } from "./FindBar.js";
import { DEFAULT_FIND_OPTIONS, clearHighlights, paintMatches, textNodes, type TextMatch } from "./find-text.js";

export function FileFind({ root, revision, truncated = false, onOpen, notice }: { root: RefObject<HTMLElement | null>; revision: unknown; truncated?: boolean; onOpen?: () => void; notice?: string }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState(DEFAULT_FIND_OPTIONS);
  const [matches, setMatches] = useState<TextMatch[]>([]);
  const [active, setActive] = useState(0);
  const [status, setStatus] = useState("");
  const trigger = useRef<HTMLElement | null>(null);
  const content = useRef<ReturnType<typeof textNodes> | undefined>(undefined);
  const close = () => { setOpen(false); clearHighlights("eidos-file-find"); trigger.current?.focus({ preventScroll: true }); };
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(); return; }
      if (event.defaultPrevented || !(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "f") {
        event.preventDefault(); event.stopPropagation(); trigger.current = document.activeElement as HTMLElement;
        setOpen(true); onOpen?.();
        const input = element.querySelector<HTMLInputElement>(".find-bar input"); input?.focus(); input?.select();
      } else if (open && event.key.toLowerCase() === "g") {
        event.preventDefault(); event.stopPropagation(); setActive((index) => matches.length ? (index + (event.shiftKey ? -1 : 1) + matches.length) % matches.length : 0);
      }
    };
    element.addEventListener("keydown", keydown);
    return () => element.removeEventListener("keydown", keydown);
  }, [root, open, matches.length, onOpen]);
  useEffect(() => {
    if (!open || !query || !root.current) { setMatches([]); setStatus(""); clearHighlights("eidos-file-find"); return; }
    let worker: Worker | undefined;
    let timeout: ReturnType<typeof setTimeout>;
    const timer = setTimeout(() => {
      if (!root.current) return;
      content.current = textNodes(root.current);
      setStatus("正在查找…");
      worker = new Worker(new URL("./find.worker.ts", import.meta.url), { type: "module" });
      timeout = setTimeout(() => { worker?.terminate(); setMatches([]); setStatus("查找超时，请缩短表达式"); }, 1000);
      worker.onmessage = (event: MessageEvent<{ matches: TextMatch[]; truncated: boolean; error?: string }>) => {
        clearTimeout(timeout); worker?.terminate(); setMatches(event.data.matches); setActive(0);
        setStatus(event.data.error ?? (event.data.truncated ? "已找到 2000 处，结果未完整" : ""));
      };
      worker.onerror = () => { clearTimeout(timeout); worker?.terminate(); setMatches([]); setStatus("无法查找文件正文"); };
      worker.postMessage({ text: content.current.text, query, options });
    }, 150);
    return () => { clearTimeout(timer); clearTimeout(timeout); worker?.terminate(); };
  }, [open, query, options, revision, root]);
  useEffect(() => {
    if (open && content.current) paintMatches(content.current.nodes, matches, active, "eidos-file-find");
    return () => clearHighlights("eidos-file-find");
  }, [matches, active, open]);
  return open ? <div className="file-find-anchor"><FindBar query={query} onQuery={setQuery} onClose={close} options={options} onOptions={setOptions} placeholder="查找文件正文…"
    onNext={() => setActive((i) => matches.length ? (i + 1) % matches.length : 0)}
    onPrevious={() => setActive((i) => matches.length ? (i - 1 + matches.length) % matches.length : 0)}
    status={notice || status || `${matches.length ? active + 1 : 0}/${matches.length}${truncated ? " · 当前预览已截断" : ""}`} /></div> : null;
}
