import { useCallback, useEffect, useRef, useState } from "react";

import type { ContextUsage, RuntimeNotification } from "../contracts.js";
import type { ContextDetails } from "../../../shared/context-management.js";
import { contextUsageFromDetails } from "../context-usage.js";

export interface ContextUsageControllerState {
  usage: ContextUsage | undefined;
  loading: boolean;
  details: ContextDetails | undefined;
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
 * Context usage is refreshed from the Runtime after durable Run, Step and
 * compaction notifications. A new Run starts with no usage until its own
 * ContextSnapshot is available.
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
  const [details, setDetails] = useState<ContextDetails>();
  const requestSequence = useRef(0);
  const refreshInFlight = useRef<number | undefined>(undefined);
  const refreshAgain = useRef(false);
  const current = useRef({ ready, sessionId, modelId, runId });
  const prevIds = useRef({ sessionId, modelId, runId });

  current.current = { ready, sessionId, modelId, runId };

  useEffect(() => {
    const sequence = ++requestSequence.current;
    refreshInFlight.current = undefined;
    refreshAgain.current = false;
    if (!ready || !sessionId || !modelId) {
      setUsage(undefined);
      setDetails(undefined);
      setLoading(false);
      prevIds.current = { sessionId, modelId, runId };
      return;
    }

    if (
      prevIds.current.sessionId !== sessionId
      || prevIds.current.modelId !== modelId
      || prevIds.current.runId !== runId
    ) {
      setUsage(undefined);
      setDetails(undefined);
      prevIds.current = { sessionId, modelId, runId };
    }

    if (!runId) { setLoading(false); return; }

    setLoading(true);
    void window.eidosRuntime.readContextDetails(runId).then((next) => {
      if (sequence !== requestSequence.current) return;
      setDetails(next);
      setUsage(contextUsageFromDetails(next));
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
      || state.runId !== targetRunId
    ) {
      return;
    }
    if (refreshInFlight.current !== undefined) {
      refreshAgain.current = true;
      return;
    }
    const sequence = ++requestSequence.current;
    refreshInFlight.current = sequence;
    setLoading(true);
    void window.eidosRuntime.readContextDetails(targetRunId).then((next) => {
      const latest = current.current;
      if (
        sequence !== requestSequence.current
        || latest.sessionId !== targetSessionId
        || latest.modelId !== targetModelId
        || latest.runId !== targetRunId
      ) {
        return;
      }
      setDetails(next);
      setUsage(contextUsageFromDetails(next));
    }).catch(() => {
      // preserve current usage
    }).finally(() => {
      if (refreshInFlight.current === sequence) {
        refreshInFlight.current = undefined;
        if (sequence === requestSequence.current) setLoading(false);
        if (refreshAgain.current) {
          refreshAgain.current = false;
          refreshFromRunId(targetRunId, targetSessionId, targetModelId);
        }
      }
    });
  }, []);

  const handleNotification = useCallback((notification: RuntimeNotification): void => {
    if (notification.method === "context/compacted") {
      const { runId, sessionId } = notification.params;
      const state = current.current;
      if (state.sessionId === sessionId && state.modelId) {
        refreshFromRunId(runId, sessionId, state.modelId);
      }
      return;
    }

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

  return [{ usage, details, loading }, { handleNotification }];
}
