import { useMemo, useRef } from "react";

type Handler = (...args: never[]) => unknown;

/**
 * Returns handlers whose identity never changes and that always call the
 * latest version. Memoized children keep their props stable even when the
 * parent re-renders for every streamed delta. The key set must stay fixed.
 */
export function useStableHandlers<T extends Record<string, Handler>>(handlers: T): T {
  const latest = useRef(handlers);
  latest.current = handlers;
  return useMemo(() => {
    const stable: Record<string, Handler> = {};
    for (const key of Object.keys(latest.current)) {
      stable[key] = (...args: never[]) => (latest.current[key] as Handler)(...args);
    }
    return stable as T;
  }, []);
}
