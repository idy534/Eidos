import { useEffect, useState } from "react";
import type { RuntimeStatus } from "../contracts.js";
import { deriveRuntimePresentation, type RuntimePresentation } from "../session-state.js";


export interface RuntimeLifecycleState {
  status: RuntimeStatus;
  presentation: RuntimePresentation;
  isStorageReady: boolean;
}

/**
 * Subscribes to Runtime status updates and derives the presentation
 * for the Sidebar indicator, Settings page, and startup gate.
 *
 * This hook owns NO write operations — it is purely observational.
 */
export function useRuntimeLifecycle(): RuntimeLifecycleState {
  const [status, setStatus] = useState<RuntimeStatus>({ state: "starting" });

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

  const presentation = deriveRuntimePresentation(status);
  const isStorageReady =
    status.state === "ready" && status.storageHealth.state === "ready";

  return { status, presentation, isStorageReady };
}
