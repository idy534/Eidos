export interface RuntimeShutdownClient {
  shutdown(): Promise<unknown>;
  waitForExit(): Promise<number | null>;
  terminate(): void;
}

export interface ShutdownResult {
  outcome:
    | "graceful"
    | "timeout"
    | "shutdown_error"
    | "exit_error";
  forced: boolean;
}

export async function shutdownRuntime(
  client: RuntimeShutdownClient,
  options?: {
    timeoutMs?: number;
    onDiagnostic?: (
      level: "info" | "warn" | "error",
      message: string,
    ) => void;
  },
): Promise<ShutdownResult> {
  const timeoutMs = options?.timeoutMs ?? 8_000;
  const onDiagnostic = options?.onDiagnostic;

  let terminated = false;
  let settled = false;
  let timerHandle: ReturnType<typeof setTimeout> | undefined;

  function terminateOnce(): void {
    if (terminated) return;
    terminated = true;
    client.terminate();
  }

  onDiagnostic?.("info", "Beginning Runtime shutdown");

  return new Promise<ShutdownResult>((resolve) => {
    timerHandle = setTimeout(() => {
      if (settled) return;
      settled = true;
      onDiagnostic?.("warn", "Shutdown timeout reached — forcing terminate");
      terminateOnce();
      resolve({ outcome: "timeout", forced: true });
    }, timeoutMs);

    (async () => {
      try {
        await client.shutdown();
      } catch (err: unknown) {
        if (settled) return;
        settled = true;
        if (timerHandle !== undefined) {
          clearTimeout(timerHandle);
          timerHandle = undefined;
        }
        const msg = err instanceof Error ? err.message : String(err);
        onDiagnostic?.("error", `Graceful shutdown failed: ${msg}`);
        terminateOnce();
        resolve({ outcome: "shutdown_error", forced: true });
        return;
      }

      try {
        await client.waitForExit();
      } catch (err: unknown) {
        if (settled) return;
        settled = true;
        if (timerHandle !== undefined) {
          clearTimeout(timerHandle);
          timerHandle = undefined;
        }
        const msg = err instanceof Error ? err.message : String(err);
        onDiagnostic?.("error", `Wait for exit failed: ${msg}`);
        terminateOnce();
        resolve({ outcome: "exit_error", forced: true });
        return;
      }

      if (settled) return;
      settled = true;
      if (timerHandle !== undefined) {
        clearTimeout(timerHandle);
        timerHandle = undefined;
      }
      onDiagnostic?.("info", "Runtime shutdown complete");
      resolve({ outcome: "graceful", forced: false });
    })();
  });
}
