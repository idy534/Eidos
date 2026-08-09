import { useCallback, useEffect, useRef, useState } from "react";

import type { ContextUsage, RuntimeNotification, Run } from "../contracts.js";

export interface ContextUsageControllerState {
  usage: ContextUsage | undefined;
  loading: boolean;
}

export interface ContextUsageControllerActions {
  handleNotification(notification: RuntimeNotification): void;
}

interface ContextUsageControllerInput {
  ready: boolean;
  sessionId: string | undefined;
  modelId: string | undefined;
  runId: string | undefined;
}

/**
 * Reads the latest effective context projection for the selected Run.
 *
 * Context usage is deliberately refreshed from the Runtime after durable Run
 * notifications. The UI never derives it from cumulative session usage or
 * from serialized bytes in the Renderer.
 */
export function useContextUsageController({
  ready,
  sessionId,
  modelId,
  runId,
}: ContextUsageControllerInput): [
  ContextUsageControllerState,
  ContextUsageControllerActions,
] {
  const [usage, setUsage] = useState<ContextUsage | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const requestSequence = useRef(0);
  const current = useRef({ ready, sessionId, modelId, runId });
  current.current = { ready, sessionId, modelId, runId };

  useEffect(() => {
    const sequence = ++requestSequence.current;
    setUsage(undefined);
    setLoading(false);
    if (!ready || !sessionId || !modelId || !runId) {
      return;
    }

    setLoading(true);
    void window.eidosRuntime.readContextUsage(runId).then((next) => {
      if (sequence !== requestSequence.current) return;
      setUsage(next ?? undefined);
    }).catch(() => {
      if (sequence === requestSequence.current) setUsage(undefined);
    }).finally(() => {
      if (sequence === requestSequence.current) setLoading(false);
    });
  }, [modelId, ready, runId, sessionId]);

  const refreshFromRun = useCallback((run: Run, clear: boolean): void => {
    const state = current.current;
    if (
      !state.ready
      || state.sessionId !== run.sessionId
      || state.modelId !== run.modelId
    ) {
      return;
    }
    const sequence = ++requestSequence.current;
    if (clear) setUsage(undefined);
    setLoading(true);
    void window.eidosRuntime.readContextUsage(run.id).then((next) => {
      const latest = current.current;
      if (
        sequence !== requestSequence.current
        || latest.sessionId !== run.sessionId
        || latest.modelId !== run.modelId
      ) {
        return;
      }
      setUsage(next ?? undefined);
    }).catch(() => {
      if (sequence === requestSequence.current) setUsage(undefined);
    }).finally(() => {
      if (sequence === requestSequence.current) setLoading(false);
    });
  }, []);

  const handleNotification = useCallback((notification: RuntimeNotification): void => {
    if (
      notification.method === "run/started"
      || notification.method === "run/updated"
      || notification.method === "run/completed"
    ) {
      refreshFromRun(notification.params.run, notification.method === "run/started");
      return;
    }
    if (notification.method === "item/completed") {
      const state = current.current;
      if (state.runId === notification.params.runId && state.sessionId) {
        void window.eidosRuntime.readContextUsage(notification.params.runId).then((next) => {
          if (
            current.current.runId === notification.params.runId
            && current.current.sessionId === notification.params.sessionId
          ) {
            setUsage(next ?? undefined);
          }
        }).catch(() => undefined);
      }
    }
  }, [refreshFromRun]);

  return [{ usage, loading }, { handleNotification }];
}
