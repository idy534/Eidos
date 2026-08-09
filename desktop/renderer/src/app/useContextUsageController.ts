import { useCallback, useEffect, useRef, useState } from "react";

import type { ContextUsage, RuntimeNotification } from "../contracts.js";

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
 * Context usage is refreshed from the Runtime after durable Run & Step
 * notifications. It maintains real-time continuity during execution
 * without clearing to empty during active runs.
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
  const prevIds = useRef({ sessionId, modelId });

  current.current = { ready, sessionId, modelId, runId };

  useEffect(() => {
    if (!ready || !sessionId || !modelId) {
      setUsage(undefined);
      setLoading(false);
      prevIds.current = { sessionId, modelId };
      return;
    }

    if (prevIds.current.sessionId !== sessionId || prevIds.current.modelId !== modelId) {
      setUsage(undefined);
      prevIds.current = { sessionId, modelId };
    }

    if (!runId) return;

    const sequence = ++requestSequence.current;
    setLoading(true);
    void window.eidosRuntime.readContextUsage(runId).then((next) => {
      if (sequence !== requestSequence.current) return;
      if (next) {
        setUsage(next);
      }
    }).catch(() => {
      // preserve current usage on error
    }).finally(() => {
      if (sequence === requestSequence.current) setLoading(false);
    });
  }, [modelId, ready, runId, sessionId]);

  const refreshFromRunId = useCallback((targetRunId: string, targetSessionId: string, targetModelId: string): void => {
    const state = current.current;
    if (
      !state.ready
      || state.sessionId !== targetSessionId
      || state.modelId !== targetModelId
    ) {
      return;
    }
    const sequence = ++requestSequence.current;
    setLoading(true);
    void window.eidosRuntime.readContextUsage(targetRunId).then((next) => {
      const latest = current.current;
      if (
        sequence !== requestSequence.current
        || latest.sessionId !== targetSessionId
        || latest.modelId !== targetModelId
      ) {
        return;
      }
      if (next) {
        setUsage(next);
      }
    }).catch(() => {
      // preserve current usage
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
      const run = notification.params.run;
      refreshFromRunId(run.id, run.sessionId, run.modelId);
      return;
    }

    if (
      notification.method === "item/started"
      || notification.method === "item/delta"
      || notification.method === "item/completed"
      || notification.method === "approval/requested"
      || notification.method === "approval/resolved"
    ) {
      const state = current.current;
      const targetRunId = "runId" in notification.params ? notification.params.runId : undefined;
      const targetSessionId = "sessionId" in notification.params ? notification.params.sessionId : undefined;
      if (targetRunId && targetSessionId && state.sessionId === targetSessionId && state.modelId) {
        refreshFromRunId(targetRunId, targetSessionId, state.modelId);
      }
    }
  }, [refreshFromRunId]);

  return [{ usage, loading }, { handleNotification }];
}
