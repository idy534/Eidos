import { useCallback, useEffect, useState } from "react";
import type { RuntimeStatus } from "../contracts.js";
import { deriveRuntimePresentation, type RuntimePresentation } from "../session-state.js";


export interface RuntimeLifecycleState {
  status: RuntimeStatus;
  presentation: RuntimePresentation;
  isStorageReady: boolean;
  restarting: boolean;
  restartRuntime(): Promise<void>;
}

/**
 * Subscribes to Runtime status updates and derives the presentation
 * for the Sidebar indicator, Settings page, and startup gate.
 *
 * This hook owns NO write operations — it is purely observational.
 */
export function useRuntimeLifecycle(): RuntimeLifecycleState {
  const [status, setStatus] = useState<RuntimeStatus>({ state: "starting" });
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    // Initial fetch
    void window.eidosRuntime.getStatus().then(setStatus).catch((cause: unknown) => {
      const message = cause instanceof Error ? cause.message : String(cause);
      setStatus({ state: "error", message });
    });

    // Subscribe to future updates
    const unsubscribe = window.eidosRuntime.onStatus(setStatus);
    return unsubscribe;
  }, []);

  const restartRuntime = useCallback(async (): Promise<void> => {
    setRestarting(true);
    try {
      setStatus(await window.eidosRuntime.restartRuntime());
    } catch (cause: unknown) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setStatus({ state: "error", message });
    } finally {
      setRestarting(false);
    }
  }, []);

  const presentation = deriveRuntimePresentation(status);
  const isStorageReady =
    status.state === "ready" && status.storageHealth.state === "ready";

  return { status, presentation, isStorageReady, restarting, restartRuntime };
}
