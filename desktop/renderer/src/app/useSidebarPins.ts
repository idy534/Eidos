import { useCallback, useEffect, useRef, useState } from "react";
import type { SidebarPinsResponse, SidebarTarget } from "../../../shared/sidebar.js";
import { sidebarTargetKey, userFacingError } from "../session-state.js";

export function useSidebarPins(enabled: boolean) {
  const [pins, setPins] = useState<SidebarTarget[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string>();
  const epoch = useRef(0);
  const loadId = useRef(0);
  const revision = useRef(0);
  const pendingTargets = useRef(new Set<string>());
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const accept = useCallback((snapshot: SidebarPinsResponse, requestEpoch: number) => {
    if (requestEpoch !== epoch.current || !enabledRef.current || snapshot.throughEventId < revision.current) return;
    revision.current = snapshot.throughEventId;
    setPins(snapshot.items);
    setLoaded(true);
  }, []);

  const refresh = useCallback(async () => {
    if (!enabledRef.current) return;
    const requestEpoch = epoch.current;
    const requestId = ++loadId.current;
    try {
      const result = await window.eidosRuntime.listSidebarPins();
      if (requestId !== loadId.current || requestEpoch !== epoch.current) return;
      accept(result, requestEpoch);
    } catch (cause) {
      if (requestId === loadId.current && requestEpoch === epoch.current) setError(userFacingError(cause));
    }
  }, [accept]);

  useEffect(() => {
    epoch.current++;
    revision.current = 0;
    pendingTargets.current.clear();
    setPending(new Set());
    setLoaded(false);
    setError(undefined);
    if (enabled) void refresh();
    return () => { epoch.current++; };
  }, [enabled, refresh]);

  const setPinned = useCallback(async (target: SidebarTarget, pinned: boolean) => {
    const key = sidebarTargetKey(target);
    if (!enabledRef.current || pendingTargets.current.has(key)) return;
    const requestEpoch = epoch.current;
    pendingTargets.current.add(key);
    setPending(new Set(pendingTargets.current));
    setError(undefined);
    try {
      const result = await window.eidosRuntime.setSidebarPinned({
        target, pinned, operationId: crypto.randomUUID(),
      });
      accept(result, requestEpoch);
    } catch (cause) {
      if (requestEpoch === epoch.current) setError(userFacingError(cause));
    } finally {
      if (requestEpoch === epoch.current) {
        pendingTargets.current.delete(key);
        setPending(new Set(pendingTargets.current));
      }
    }
  }, [accept]);

  const handleChanged = useCallback((eventId: number) => {
    if (eventId <= revision.current) return;
    revision.current = eventId;
    void refresh();
  }, [refresh]);

  const clearError = useCallback(() => setError(undefined), []);
  return { pins, loaded, pending, error, setPinned, handleChanged, clearError };
}
