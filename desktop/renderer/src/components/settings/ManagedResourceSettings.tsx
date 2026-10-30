import { useEffect, useRef, useState } from "react";
import type { ResourceProgress, ResourceStatus } from "../../../../shared/managed-resources.js";
import { userFacingError } from "../../session-state.js";

const PHASES: Record<ResourceProgress["phase"], string> = {
  missing: "按需准备", checking: "检查中", downloading: "下载中",
  verifying: "校验中", extracting: "安装中", ready: "已就绪", failed: "准备失败",
};

export function ManagedResourceSettings() {
  const [status, setStatus] = useState<ResourceStatus | null>(null);
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState<string>();
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    const api = window.eidosRuntime;
    if (!api?.getResourceStatus) return () => { mounted.current = false; };
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result = await api.getResourceStatus();
        if (active) {
          setStatus(result);
          setError(undefined);
          // The process is closing or this Runtime has no managed resources.
          if (!result.available) active = false;
        }
      } catch (cause) {
        if (active) setError(userFacingError(cause));
      } finally {
        if (active) timer = setTimeout(() => void refresh(), 2000);
      }
    }
    void refresh();
    return () => { active = false; mounted.current = false; clearTimeout(timer); };
  }, []);

  async function prepare(packId: string) {
    setPending(packId);
    try {
      const response = await window.eidosRuntime.retryResource({ packId });
      if (!response.accepted) throw new Error("Runtime 暂时无法准备组件，请稍后重试。");
      const next = await window.eidosRuntime.getResourceStatus();
      if (mounted.current) { setStatus(next); setError(undefined); }
    } catch (cause) {
      if (mounted.current) setError(userFacingError(cause));
    } finally {
      if (mounted.current) setPending(undefined);
    }
  }

  if ((!status || !status.available) && !error) return null;
  const packs = status?.packs ?? [];
  const ready = packs.filter((pack) => pack.phase === "ready").length;
  return (
    <details className="managed-resources">
      <summary>文档组件<span>{ready}/{packs.length} 已就绪</span></summary>
      <p>组件会在需要时后台准备。普通聊天和其他任务可以继续使用。</p>
      {error && <p role="status" className="managed-resources-error">{error}</p>}
      <ul aria-label="文档组件状态">
        {packs.map((pack) => {
          const busy = !["missing", "ready", "failed"].includes(pack.phase);
          const progress = pack.phase === "downloading" && pack.totalBytes
            ? ` ${Math.floor((pack.downloadedBytes ?? 0) / pack.totalBytes * 100)}%` : "";
          return (
            <li key={pack.id}>
              <span className="managed-resource-name">{pack.title}</span>
              <span role="status">{PHASES[pack.phase]}{progress}</span>
              {(pack.phase === "missing" || pack.phase === "failed") && (
                <button type="button" aria-label={`${pack.phase === "failed" ? "重试" : "准备"}${pack.title}`}
                  disabled={pending === pack.id || busy} onClick={() => void prepare(pack.id)}>
                  {pending === pack.id ? "准备中…" : pack.phase === "failed" ? "重试" : "准备"}
                </button>
              )}
              {pack.errorCode === "resource_disk_full" && <small>磁盘空间不足，请清理后重试。</small>}
            </li>
          );
        })}
      </ul>
      {status?.directory && <p className="managed-resource-directory" title={status.directory}>{status.directory}</p>}
    </details>
  );
}
