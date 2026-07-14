import { useEffect, useState } from "react";

import type { RuntimeStatus } from "./runtime-status";


export function App() {
  const [status, setStatus] = useState<RuntimeStatus>({ state: "starting" });

  useEffect(() => {
    const unsubscribe = window.eidosRuntime.onStatus(setStatus);
    void window.eidosRuntime.getStatus().then(setStatus).catch((error: unknown) => {
      setStatus({
        state: "error",
        message: error instanceof Error ? error.message : "无法读取 Runtime 状态。",
      });
    });
    return unsubscribe;
  }, []);

  return (
    <main className="shell">
      <section className="intro" aria-labelledby="page-title">
        <p className="eyebrow">Developer Preview · L0</p>
        <h1 id="page-title">Eidos</h1>
        <p className="tagline">让想法拥有可执行的形态。</p>
      </section>

      <section
        className={`runtime-state runtime-state--${status.state}`}
        aria-live="polite"
        aria-atomic="true"
        role={status.state === "error" ? "alert" : "status"}
      >
        <span className="status-mark" aria-hidden="true" />
        <div>
          <p className="status-label">本地 Agent Runtime</p>
          <StatusDetails status={status} />
        </div>
      </section>

      <footer>
        当前阶段只验证桌面进程与本地 Runtime 的安全通信，尚未接入模型与工具。
      </footer>
    </main>
  );
}

function StatusDetails({ status }: { status: RuntimeStatus }) {
  if (status.state === "starting") {
    return <p className="status-detail">正在启动 Python Runtime 并完成协议握手…</p>;
  }
  if (status.state === "error") {
    return (
      <>
        <p className="status-title">Runtime 启动失败</p>
        <p className="status-detail">{status.message}</p>
      </>
    );
  }
  return (
    <>
      <p className="status-title">Runtime 已就绪</p>
      <p className="status-detail">
        协议 v{status.protocolVersion} · Runtime {status.runtimeVersion} · Shell
        {status.runShell ? " 可用" : " 暂未启用"}
      </p>
    </>
  );
}
