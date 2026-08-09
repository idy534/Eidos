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
  const requestIsCurrent = useCallback(
    (
      sequence: number,
      requestedRunId: string,
      requestedSessionId: string,
      requestedModelId: string,
    ): boolean => {
      const latest = current.current;
      return sequence === requestSequence.current
        && latest.ready
        && latest.runId === requestedRunId
        && latest.sessionId === requestedSessionId
        && latest.modelId === requestedModelId;
    },
    [],
  );

  useEffect(() => {
    const sequence = ++requestSequence.current;
    setUsage(undefined);
    setLoading(false);
    if (!ready || !sessionId || !modelId || !runId) {
      return;
    }

    const requestedRunId = runId;
    const requestedSessionId = sessionId;
    const requestedModelId = modelId;
    setLoading(true);
    void window.eidosRuntime.readContextUsage(requestedRunId).then((next) => {
      if (!requestIsCurrent(
        sequence,
        requestedRunId,
        requestedSessionId,
        requestedModelId,
      )) return;
      setUsage(next ?? undefined);
    }).catch(() => {
      if (requestIsCurrent(
        sequence,
        requestedRunId,
        requestedSessionId,
        requestedModelId,
      )) setUsage(undefined);
    }).finally(() => {
      if (requestIsCurrent(
        sequence,
        requestedRunId,
        requestedSessionId,
        requestedModelId,
      )) setLoading(false);
    });
  }, [modelId, ready, requestIsCurrent, runId, sessionId]);

  const refreshFromRun = useCallback((run: Run, clear: boolean): void => {
    const state = current.current;
    if (
      !state.ready
      || state.sessionId !== run.sessionId
      || state.modelId !== run.modelId
      || state.runId !== run.id
    ) {
      return;
    }
    const sequence = ++requestSequence.current;
    const requestedRunId = run.id;
    const requestedSessionId = run.sessionId;
    const requestedModelId = run.modelId;
    if (clear) setUsage(undefined);
    setLoading(true);
    void window.eidosRuntime.readContextUsage(requestedRunId).then((next) => {
      if (!requestIsCurrent(
        sequence,
        requestedRunId,
        requestedSessionId,
        requestedModelId,
      )) return;
      setUsage(next ?? undefined);
    }).catch(() => {
      if (requestIsCurrent(
        sequence,
        requestedRunId,
        requestedSessionId,
        requestedModelId,
      )) setUsage(undefined);
    }).finally(() => {
      if (requestIsCurrent(
        sequence,
        requestedRunId,
        requestedSessionId,
        requestedModelId,
      )) setLoading(false);
    });
  }, [requestIsCurrent]);

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
      if (
        state.runId === notification.params.runId
        && state.sessionId === notification.params.sessionId
        && state.modelId
      ) {
        const sequence = ++requestSequence.current;
        const requestedRunId = notification.params.runId;
        const requestedSessionId = notification.params.sessionId;
        const requestedModelId = state.modelId;
        setLoading(true);
        void window.eidosRuntime.readContextUsage(requestedRunId).then((next) => {
          if (!requestIsCurrent(
            sequence,
            requestedRunId,
            requestedSessionId,
            requestedModelId,
          )) return;
          setUsage(next ?? undefined);
        }).catch(() => {
          if (requestIsCurrent(
            sequence,
            requestedRunId,
            requestedSessionId,
            requestedModelId,
          )) setUsage(undefined);
        }).finally(() => {
          if (requestIsCurrent(
            sequence,
            requestedRunId,
            requestedSessionId,
            requestedModelId,
          )) setLoading(false);
        });
      }
    }
  }, [requestIsCurrent, refreshFromRun]);

  return [{ usage, loading }, { handleNotification }];
}
