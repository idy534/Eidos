import { useCallback, useEffect, useRef, useState } from "react";
import type { PersonalizationPreferences, PersonalizationResponse } from "../../../../shared/personalization.js";

// This is unsaved form input for the current renderer lifetime, not configuration.
let interruptedDraft: { saved: PersonalizationResponse; draft: PersonalizationPreferences } | undefined;
type PendingSave = { expectedRevision: number; preferences: PersonalizationPreferences; result: Promise<PersonalizationResponse> };
let pendingSave: PendingSave | undefined;
export function discardPersonalizationDraft() { interruptedDraft = undefined; }
const same = (left: PersonalizationPreferences, right: PersonalizationPreferences) => JSON.stringify(left) === JSON.stringify(right);

export function usePersonalizationSettings({ ready, paused, onShowToast, onDirtyChange, onBusyChange }: {
  ready: boolean;
  paused: boolean;
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
  onDirtyChange?: ((dirty: boolean) => void) | undefined;
  onBusyChange?: ((busy: boolean) => void) | undefined;
}) {
  const [saved, setSaved] = useState<PersonalizationResponse | undefined>(interruptedDraft?.saved);
  const [draft, setDraft] = useState<PersonalizationPreferences>(interruptedDraft?.draft ?? {});
  const [busy, setBusy] = useState<"load" | "save" | null>(ready ? "load" : null);
  const [error, setError] = useState("");
  const mounted = useRef(false);
  const working = useRef(false);
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const savedRef = useRef(saved);
  const draftRef = useRef(draft);
  const blocked = useRef(false);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dirty = Boolean(saved && !same(saved.preferences, draft));
  const languageError = draft.responseLanguage === "custom" && !draft.customLanguage?.trim();

  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange?.(busy !== null); }, [busy, onBusyChange]);

  function cacheDraft() {
    interruptedDraft = savedRef.current && !same(savedRef.current.preferences, draftRef.current)
      ? { saved: savedRef.current, draft: draftRef.current } : undefined;
  }
  function replaceSaved(value: PersonalizationResponse) {
    savedRef.current = value; draftRef.current = value.preferences;
    setSaved(value); setDraft(value.preferences); discardPersonalizationDraft();
  }

  const executeSave = useCallback(async (preferences: PersonalizationPreferences) => {
    if (!readyRef.current || pausedRef.current || !savedRef.current || working.current || blocked.current) return;
    if (preferences.responseLanguage === "custom" && !preferences.customLanguage?.trim()) return;
    if (same(savedRef.current.preferences, preferences)) return;
    working.current = true; setBusy("save"); setError("");
    let ticket: PendingSave | undefined;
    try {
      const expectedRevision = savedRef.current.revision;
      ticket = { expectedRevision, preferences, result: window.eidosRuntime.savePersonalization({ expectedRevision, preferences }) };
      pendingSave = ticket;
      const value = await ticket.result;
      if (mounted.current) {
        if (same(draftRef.current, preferences)) replaceSaved(value);
        else { savedRef.current = value; setSaved(value); cacheDraft(); }
        onShowToast?.("个性化设置已保存，将用于新任务。", "success");
      } else if (interruptedDraft?.saved.revision === expectedRevision) {
        interruptedDraft = same(interruptedDraft.draft, value.preferences) ? undefined : { saved: value, draft: interruptedDraft.draft };
      }
    } catch (cause) {
      blocked.current = true;
      if (mounted.current) {
        const code = cause instanceof Error ? cause.message : "";
        setError(code.includes("PERSONALIZATION_CONFLICT")
          ? "个性化设置已在其他位置更新。当前修改已保留，请重新读取后再编辑。"
          : code.includes("SENSITIVE_CONTENT_REJECTED")
            ? "内容包含凭据等敏感信息。当前修改已保留，请移除后再保存。"
            : "个性化设置保存失败。当前修改已保留，请重试。");
      }
    } finally {
      if (pendingSave === ticket) pendingSave = undefined;
      working.current = false;
      if (mounted.current) {
        setBusy(null);
        if (!blocked.current && !debounce.current) void executeSave(draftRef.current);
      }
    }
  }, [onShowToast]);

  const load = useCallback(async (preserveDraft = false) => {
    if (working.current || !ready) return;
    working.current = true; setBusy("load"); setError("");
    try {
      const outstanding = pendingSave;
      if (outstanding) await outstanding.result.catch(() => undefined);
      const value = await window.eidosRuntime.readPersonalization();
      if (mounted.current) {
        blocked.current = false;
        if (preserveDraft && savedRef.current && !same(value.preferences, draftRef.current)) {
          if (outstanding && value.revision === outstanding.expectedRevision + 1 && same(value.preferences, outstanding.preferences)) {
            savedRef.current = value; setSaved(value); cacheDraft();
          } else if (value.revision !== savedRef.current.revision) {
            blocked.current = true;
            setError("个性化设置已在其他位置更新。当前修改已保留，请重新读取后再编辑。");
          }
        } else replaceSaved(value);
      }
    } catch {
      blocked.current = true;
      if (mounted.current) setError("个性化设置无法读取。请重新读取。");
    } finally {
      working.current = false;
      if (mounted.current) {
        setBusy(null);
        if (preserveDraft && !blocked.current && !debounce.current) void executeSave(draftRef.current);
      }
    }
  }, [ready, executeSave]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; if (debounce.current) clearTimeout(debounce.current); };
  }, []);
  useEffect(() => { if (ready) void load(Boolean(savedRef.current)); }, [ready, load]);
  useEffect(() => {
    if (paused && debounce.current) { clearTimeout(debounce.current); debounce.current = null; }
    if (!paused) void executeSave(draftRef.current);
  }, [paused, executeSave]);

  function update<K extends keyof PersonalizationPreferences>(key: K, value: PersonalizationPreferences[K], immediate = false) {
    const next = { ...draftRef.current, [key]: value };
    draftRef.current = next; setDraft(next); cacheDraft();
    // A new edit is an explicit retry. A failed save alone never starts a loop.
    blocked.current = false; setError("");
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = null;
    if (immediate) void executeSave(next);
    else debounce.current = setTimeout(() => { debounce.current = null; void executeSave(draftRef.current); }, 400);
  }
  function handleBlur() {
    if (!debounce.current) return;
    clearTimeout(debounce.current); debounce.current = null;
    void executeSave(draftRef.current);
  }
  return { saved, draft, busy, error, dirty, languageError, load, update, handleBlur };
}
