import { AppShell } from "./app/AppShell.js";
import { EidosMark } from "./components/EidosMark.js";
import { useRuntimeLifecycle } from "./app/useRuntimeLifecycle.js";
import { deriveRuntimePresentation } from "./session-state.js";
import type { RuntimeStatus } from "./contracts.js";

/**
 * App is the outermost shell that handles the runtime gate.
 *
 * Once the runtime is ready, it renders AppShell which owns all domain logic.
 */
export function App() {
  const { status } = useRuntimeLifecycle();

  if (status.state !== "ready") {
    return <RuntimeGate status={status} />;
  }

  return <AppShell />;
}

function RuntimeGate({ status }: { status: RuntimeStatus }) {
  const pres = deriveRuntimePresentation(status);
  return (
    <main className="runtime-gate" role={status.state === "error" ? "alert" : "status"}>
      <div className="runtime-gate-card">
        <EidosMark className="runtime-logo" variant="hero" />
        <p className="eyebrow">Eidos · Local Agent Runtime</p>
        <h1>{status.state === "error" ? "启动失败" : "正在启动 Engine"}</h1>
        <p className="runtime-gate-desc">{pres.description ?? "正在建立安全沙箱与本地 Runtime 协议握手…"}</p>
        {status.state !== "error" && (
          <div className="runtime-progress-bar">
            <div className="runtime-progress-pulse" />
          </div>
        )}
      </div>
    </main>
  );
}
