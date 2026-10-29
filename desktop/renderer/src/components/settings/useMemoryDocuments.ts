import { useCallback, useEffect, useRef, useState } from "react";
import type { MemoryState } from "../../../../shared/memory.generated.js";

export type MemoryScopeChoice = "current" | "global";

export function useMemoryDocuments(projectId: string | undefined, scope: MemoryScopeChoice, enabled = true) {
  const [state, setState] = useState<MemoryState>();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  const stateRef = useRef(state);
  stateRef.current = state;
  const refresh = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    setCursor(0); setQuery(""); setState(undefined);
  }, [projectId, scope]);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    const timer = window.setTimeout(() => {
      if (document.visibilityState === "hidden") return;
      setLoading(true); setError("");
      void window.eidosRuntime.memory("memory/list", {
        projectId: projectId ?? null, scope, query, cursor, includeArchived: true,
      }).then((value) => { if (active) setState(value); })
        .catch(() => { if (active) setError("记忆无法读取，请重试。"); })
        .finally(() => { if (active) setLoading(false); });
    }, query ? 250 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [projectId, scope, query, cursor, version, enabled]);

  useEffect(() => {
    const unsubscribe = window.eidosRuntime.onNotification?.((notice) => {
      if (notice.method === "memory/changed"
          && (!stateRef.current || stateRef.current.scopes.some((value) => value.id === notice.params.scopeId))) refresh();
    });
    const visible = () => { if (document.visibilityState !== "hidden") refresh(); };
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      unsubscribe?.(); window.removeEventListener("focus", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [refresh]);

  return { state, query, setQuery: (value: string) => { setQuery(value); setCursor(0); },
    cursor, setCursor, loading, error, refresh };
}
