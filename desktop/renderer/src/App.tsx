import { AppShell } from "./app/AppShell.js";
import { EidosMark } from "./components/EidosMark.js";
import { Button } from "./components/Button.js";
import { useRuntimeLifecycle } from "./app/useRuntimeLifecycle.js";
import { deriveRuntimePresentation } from "./session-state.js";
import type { RuntimeStatus } from "./contracts.js";

/**
 * App is the outermost shell that handles the runtime gate.
 *
 * It holds the single authoritative subscription to the Runtime lifecycle.
 * Once the runtime is ready, it renders AppShell.
 */
export function App() {
  const runtime = useRuntimeLifecycle();

  if (runtime.status.state !== "ready") {
    return (
      <RuntimeGate
        status={runtime.status}
        restarting={runtime.restarting}
        onRestart={runtime.restartRuntime}
      />
    );
  }

  return <AppShell runtime={runtime} />;
}

function RuntimeGate({
  status,
  restarting,
  onRestart,
}: {
  status: RuntimeStatus;
  restarting: boolean;
  onRestart: () => Promise<void>;
}) {
  const pres = deriveRuntimePresentation(status);
  return (
    <main className="runtime-gate" role={status.state === "error" ? "alert" : "status"}>
      <div className="runtime-gate-card">
        <EidosMark className="runtime-logo" variant="hero" />
        <p className="eyebrow">Eidos · Local Agent Runtime</p>
        <h1>{status.state === "error" ? "启动失败" : "正在启动 Engine"}</h1>
        <p className="runtime-gate-desc">{pres.description ?? "正在建立沙箱，与Runtime协议握手…"}</p>
        {status.state !== "error" && (
          <div className="runtime-progress-bar">
            <div className="runtime-progress-pulse" />
          </div>
        )}
        {status.state === "error" && (
          <div className="runtime-gate-actions">
            <Button
              variant="primary"
              loading={restarting}
              onClick={() => void onRestart()}
            >
              {restarting ? "正在重新启动…" : "重新启动 Runtime"}
            </Button>
          </div>
        )}
      </div>
    </main>
  );
}
