import { useCallback, useEffect, useRef, useState } from "react";
import type { QueuedRunChange, QueuedRunListResponse } from "../../../shared/queued-runs.js";
import { userFacingError } from "../session-state.js";

export function useQueuedMessages(sessionId: string, ready: boolean) {
  const [page, setPage] = useState<QueuedRunListResponse>({ items: [] });
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const refreshPending = useRef(false);
  const reading = useRef<number | undefined>(undefined);
  const mounted = useRef(false);

  const load = useCallback(async (afterSequence?: number): Promise<void> => {
    if (!ready || !window.eidosRuntime?.listQueuedRuns) return;
    if (reading.current !== undefined) { refreshPending.current = true; return; }
    setLoading(true);
    const token = ++generation.current;
    reading.current = token;
    try {
      do {
        refreshPending.current = false;
        const result = await window.eidosRuntime.listQueuedRuns({ sessionId, ...(afterSequence ? { afterSequence } : {}) });
        if (!mounted.current || token !== generation.current) return;
        if (refreshPending.current) { afterSequence = undefined; continue; }
        setPage((previous) => ({ ...result, items: afterSequence
          ? [...previous.items.filter((item) => !result.items.some((incoming) => incoming.runId === item.runId)), ...result.items]
          : result.items }));
        setError(undefined);
      } while (refreshPending.current);
    } catch (cause) {
      if (mounted.current && token === generation.current) setError(userFacingError(cause));
    } finally {
      if (reading.current === token) reading.current = undefined;
      if (mounted.current && token === generation.current) setLoading(false);
    }
  }, [sessionId, ready]);

  useEffect(() => {
    mounted.current = true;
    void load();
    const unsubscribe = window.eidosRuntime?.onNotification?.((notification) => {
      if (notification.method === "run/queueChanged" && notification.params.change.sessionId === sessionId) {
        void load();
      } else if ((notification.method === "run/started" || notification.method === "run/updated" || notification.method === "run/completed")
        && notification.params.sessionId === sessionId) {
        if (notification.params.run.status !== "queued") {
          setPage((previous) => ({ ...previous, items: previous.items.filter((item) => item.runId !== notification.params.run.id) }));
        }
        void load();
      }
    });
    return () => { mounted.current = false; generation.current++; reading.current = undefined; refreshPending.current = false; unsubscribe?.(); };
  }, [sessionId, load]);

  const project = useCallback((change: QueuedRunChange) => {
    if (change.sessionId !== sessionId) return;
    setPage((previous) => ({ ...previous, items: change.kind === "deleted"
      ? previous.items.filter((item) => item.runId !== change.runId)
      : previous.items.map((item) => item.runId === change.runId ? { ...item, userInput: change.userInput } : item) }));
    void load();
  }, [sessionId, load]);

  return { ...page, error, loading, refresh: load, project };
}
