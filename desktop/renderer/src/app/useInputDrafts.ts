import { useCallback, useEffect, useRef, useState } from "react";
import type { InputDraft, InputReference } from "../../../shared/input-context.js";

const EMPTY: InputDraft = { text: "", references: [] };
const storageKey = (id: string): string => id.startsWith("draft-") ? "new-conversation" : id;

/** Serializes writes per draft. A late hydration cannot replace local edits. */
export function useInputDrafts(sessionId: string | undefined, storageReady: boolean) {
  const [drafts, setDrafts] = useState<Record<string, InputDraft>>({});
  const values = useRef(drafts);
  const touched = useRef(new Set<string>());
  const loading = useRef(new Set<string>());
  const pending = useRef(new Map<string, Promise<unknown>>());
  const [errors, setErrors] = useState<Record<string, string | undefined>>({});
  const draftOwner = useRef<string | undefined>(undefined);
  if (sessionId?.startsWith("draft-")) draftOwner.current = sessionId;
  const setError = useCallback((id: string, message: string | undefined) => setErrors((previous) => ({ ...previous, [id]: message })), []);
  const [loaded, setLoaded] = useState<Record<string, boolean>>({});

  const write = useCallback((id: string, draft: InputDraft) => {
    const key = storageKey(id);
    const next = (pending.current.get(key) ?? Promise.resolve()).catch(() => {}).then(() => {
      if (id.startsWith("draft-") && draftOwner.current !== id) return draft;
      return window.eidosRuntime?.writeInputDraft ? window.eidosRuntime.writeInputDraft(key, draft) : draft;
    });
    pending.current.set(key, next);
    void next.catch(() => { if (pending.current.get(key) === next) setError(id, "草稿保存失败，请保留当前窗口并重试。"); });
    return next;
  }, []);

  const update = useCallback((id: string, transform: (draft: InputDraft) => InputDraft) => {
    const next = transform(values.current[id] ?? EMPTY);
    if (next.references.length > 20) {
      setError(id, "每轮最多添加 20 个引用。");
      return;
    }
    setError(id, undefined);
    touched.current.add(id);
    values.current = { ...values.current, [id]: next };
    setDrafts(values.current);
    void write(id, next);
  }, [write]);

  useEffect(() => {
    if (!storageReady || !sessionId || loading.current.has(sessionId)) return;
    if (!window.eidosRuntime?.readInputDraft) {
      setLoaded((previous) => ({ ...previous, [sessionId]: true }));
      return;
    }
    loading.current.add(sessionId);
    void window.eidosRuntime.readInputDraft(storageKey(sessionId)).then((draft) => {
      if (!touched.current.has(sessionId)) {
        values.current = { ...values.current, [sessionId]: draft };
        setDrafts(values.current);
      }
      setLoaded((previous) => ({ ...previous, [sessionId]: true }));
    }, () => {
      loading.current.delete(sessionId);
      setError(sessionId, "草稿读取失败，请重新打开当前对话后重试。");
    });
  }, [sessionId, storageReady]);

  const add = useCallback((id: string, reference: InputReference) => update(id, (draft) => ({
    ...draft,
    references: [...draft.references.filter((value) => value.id !== reference.id && !(value.kind === reference.kind && value.source === reference.source && value.label === reference.label)), reference],
  })), [update]);

  const flush = useCallback(async (id: string) => {
    await write(id, values.current[id] ?? EMPTY);
  }, [write]);

  return { drafts, values, update, add, flush, error: sessionId ? errors[sessionId] : undefined, ready: !sessionId || loaded[sessionId] === true };
}
