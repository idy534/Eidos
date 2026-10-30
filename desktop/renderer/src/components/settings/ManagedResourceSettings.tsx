import { useEffect, useRef, useState } from "react";
import type { ResourceProgress, ResourceStatus } from "../../../../shared/managed-resources.js";
import { userFacingError } from "../../session-state.js";
import { SettingSection } from "../SettingSection.js";
import { Toggle } from "../Toggle.js";

const PHASES: Record<ResourceProgress["phase"], string> = {
  missing: "",
  checking: "检查中",
  downloading: "下载中",
  verifying: "校验中",
  extracting: "安装中",
  ready: "已就绪",
  failed: "准备失败",
};

interface ResourceCategoryDef {
  title: string;
  packIds: string[];
}

const RESOURCE_CATEGORIES: ResourceCategoryDef[] = [
  {
    title: "基础文档引擎",
    packIds: ["document-python", "document-node", "node", "office"],
  },
  {
    title: "高级渲染与扩展",
    packIds: ["data", "images", "pdf", "fonts"],
  },
  {
    title: "OCR 识别与语言包",
    packIds: ["ocr", "ocr-chi-sim", "ocr-eng"],
  },
];

let cachedStatus: ResourceStatus | null = null;

export function prefetchResourceStatus(): void {
  const api = typeof window !== "undefined" ? window.eidosRuntime : undefined;
  if (!api?.getResourceStatus) return;
  void api.getResourceStatus().then((result) => {
    cachedStatus = result;
  }).catch(() => {});
}

export function _resetResourceCacheForTest(): void {
  cachedStatus = null;
}

const AUTO_DOWNLOAD_STORAGE_KEY = "eidos.managed_resources.auto_download";

function getInitialAutoDownload(): boolean {
  try {
    const saved = localStorage.getItem(AUTO_DOWNLOAD_STORAGE_KEY);
    return saved !== null ? saved === "true" : true;
  } catch {
    return true;
  }
}

export function ManagedResourceSettings({ onShowToast }: {
  onShowToast?: ((message: string, type: "success" | "info" | "error") => void) | undefined;
} = {}) {
  const [status, setStatus] = useState<ResourceStatus | null>(() => cachedStatus);
  const [error, setError] = useState<string>();
  const [pending, setPending] = useState<string>();
  const [autoDownload, setAutoDownload] = useState<boolean>(getInitialAutoDownload);
  const mounted = useRef(false);
  const autoTriggeredPacks = useRef<Set<string>>(new Set());

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
          cachedStatus = result;
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
      if (!response.accepted) throw new Error("Runtime 暂时无法下载组件，请稍后重试。");
      const next = await window.eidosRuntime.getResourceStatus();
      cachedStatus = next;
      if (mounted.current) { setStatus(next); setError(undefined); }
    } catch (cause) {
      if (mounted.current) setError(userFacingError(cause));
    } finally {
      if (mounted.current) setPending(undefined);
    }
  }

  // Auto-download missing packs when autoDownload is enabled
  useEffect(() => {
    if (!autoDownload || !status?.available) return;
    const packs = status.packs ?? [];
    const missingPacks = packs.filter((p) => p.phase === "missing");
    for (const pack of missingPacks) {
      if (!autoTriggeredPacks.current.has(pack.id)) {
        autoTriggeredPacks.current.add(pack.id);
        void prepare(pack.id);
      }
    }
  }, [autoDownload, status]);

  function handleToggleAutoDownload(next: boolean) {
    setAutoDownload(next);
    try {
      localStorage.setItem(AUTO_DOWNLOAD_STORAGE_KEY, String(next));
    } catch {}
    if (next) {
      autoTriggeredPacks.current.clear();
      onShowToast?.("已开启自动预下载，缺失组件将自动在后台下载", "info");
    }
  }

  if (status && !status.available && !error) return null;
  const packs = status?.packs ?? [];
  const ready = packs.filter((pack) => pack.phase === "ready").length;

  // Group packs
  const assigned = new Set<string>();
  const groupedPacks = RESOURCE_CATEGORIES.map((cat) => {
    const matched = packs.filter((p) => cat.packIds.includes(p.id));
    matched.forEach((p) => assigned.add(p.id));
    return {
      title: cat.title,
      packs: matched,
    };
  });
  const unassigned = packs.filter((p) => !assigned.has(p.id));
  if (unassigned.length > 0) {
    groupedPacks.push({
      title: "其他组件",
      packs: unassigned,
    });
  }

  return (
    <SettingSection
      title="运行时组件与依赖"
      headerAction={
        <div className="managed-resources-auto-toggle">
          <span
            className="managed-resources-auto-toggle-label"
            onClick={() => handleToggleAutoDownload(!autoDownload)}
          >
            自动预下载
          </span>
          <Toggle
            checked={autoDownload}
            onChange={handleToggleAutoDownload}
            label="自动预下载"
          />
        </div>
      }
      className="managed-resources-section"
    >
      <details className="managed-resources-mac">
        <summary className="managed-resources-summary">
          <div className="managed-resources-summary-left">
            <span className="managed-resources-title">文档组件</span>
            <span className="managed-resources-desc">
              Word、Excel、PPT 与 PDF 处理所需的运行依赖与本地离线资源
            </span>
          </div>
          <div className="managed-resources-summary-right">
            <span className={`managed-resources-badge ${ready === packs.length && packs.length > 0 ? "managed-resources-badge--ready" : ""}`}>
              <span className="managed-resources-dot" aria-hidden="true" />
              <span className="managed-resources-status-count">
                {status ? `${ready}/${packs.length} 已就绪` : "检查中…"}
              </span>
            </span>
            <span className="managed-resources-chevron" aria-hidden="true">›</span>
          </div>
        </summary>

        <div className="managed-resources-content" aria-label="文档组件状态">
          {error && <p role="status" className="managed-resources-error">{error}</p>}

          <div className="managed-resources-groups">
            {groupedPacks.map((group) => {
              if (group.packs.length === 0) return null;
              const groupReady = group.packs.filter((p) => p.phase === "ready").length;
              return (
                <div key={group.title} className="managed-resource-group">
                  <div className="managed-resource-group-header">
                    <span className="managed-resource-group-title">{group.title}</span>
                    <span className="managed-resource-group-stat">
                      {groupReady}/{group.packs.length} 已就绪
                    </span>
                  </div>
                  <ul className="managed-resource-list">
                    {group.packs.map((pack) => {
                      const busy = !["missing", "ready", "failed"].includes(pack.phase);
                      const phaseText = PHASES[pack.phase];
                      const progress = pack.phase === "downloading" && pack.totalBytes
                        ? ` ${Math.floor((pack.downloadedBytes ?? 0) / pack.totalBytes * 100)}%` : "";
                      const buttonLabel = pending === pack.id ? "下载中…" : pack.phase === "failed" ? "重试" : "下载";
                      const ariaLabel = `${pack.phase === "failed" ? "重试" : "下载"}${pack.title}`;

                      return (
                        <li key={pack.id} className="managed-resource-item">
                          <div className="managed-resource-info">
                            <span className="managed-resource-name">{pack.title}</span>
                            {(phaseText || progress) && (
                              <span
                                role="status"
                                className={`managed-resource-phase managed-resource-phase--${pack.phase}`}
                              >
                                {phaseText}{progress}
                              </span>
                            )}
                          </div>
                          {(pack.phase === "missing" || pack.phase === "failed") && (
                            <button
                              type="button"
                              className="managed-resource-btn"
                              aria-label={ariaLabel}
                              disabled={pending === pack.id || busy}
                              onClick={() => void prepare(pack.id)}
                            >
                              {buttonLabel}
                            </button>
                          )}
                          {pack.errorCode === "resource_disk_full" && (
                            <small className="managed-resource-error-hint">磁盘空间不足，请清理后重试。</small>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </div>
        </div>
      </details>
    </SettingSection>
  );
}
