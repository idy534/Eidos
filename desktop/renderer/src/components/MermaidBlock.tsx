import { memo, useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import mermaid from "mermaid";
import { DropdownMenu } from "./DropdownMenu.js";
import { Tooltip } from "./Tooltip.js";
import {
  CopyIcon,
  CheckmarkIcon,
  ExpandIcon,
  CloseIcon,
  MoreHorizontalIcon,
  DownloadIcon,
  CodeIcon,
  ZoomInIcon,
  ZoomOutIcon,
  ResetZoomIcon,
} from "./MenuIcons.js";


let mermaidInitialized = false;

function cleanStrayMermaidElements(containerId: string) {
  if (typeof document === "undefined") return;
  try {
    const selector = `body > [id*="${containerId}"], body > [id*="dmermaid"], body > [id*="mermaid-error"]`;
    const strays = document.querySelectorAll(selector);
    strays.forEach((el) => {
      if (el.parentNode === document.body) {
        el.parentNode.removeChild(el);
      }
    });
  } catch {}
}

const mermaidSvgCache = new Map<string, string>();

function downloadSvg(svgString: string, filename = "diagram.svg") {
  if (typeof document === "undefined") return;
  try {
    const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    console.error("Failed to download svg:", err);
  }
}

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 5.0;
const ZOOM_DEFAULT = 1.0;
const ZOOM_STEP = 0.25;

export function MermaidModal({
  svg,
  code,
  onClose,
}: {
  svg: string;
  code: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [zoom, setZoom] = useState(ZOOM_DEFAULT);
  const [isDragging, setIsDragging] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const dragStartRef = useRef<{ x: number; y: number; scrollLeft: number; scrollTop: number } | null>(null);

  const handleZoomIn = useCallback(() => {
    setZoom((prev) => {
      const next = Math.min(ZOOM_MAX, (Math.floor(prev / ZOOM_STEP + 0.001) + 1) * ZOOM_STEP);
      return Math.round(next * 100) / 100;
    });
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoom((prev) => {
      const next = Math.max(ZOOM_MIN, (Math.ceil(prev / ZOOM_STEP - 0.001) - 1) * ZOOM_STEP);
      return Math.round(next * 100) / 100;
    });
  }, []);

  const handleResetZoom = useCallback(() => {
    setZoom(ZOOM_DEFAULT);
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      const isMod = event.ctrlKey || event.metaKey;
      const isZoomIn = (isMod && (event.key === "=" || event.key === "+")) || (!isMod && !event.altKey && (event.key === "+" || event.key === "="));
      const isZoomOut = (isMod && (event.key === "-" || event.key === "_")) || (!isMod && !event.altKey && (event.key === "-" || event.key === "_"));
      const isZoomReset = (isMod && event.key === "0") || (!isMod && !event.altKey && event.key === "0");

      if (isZoomIn) {
        event.preventDefault();
        handleZoomIn();
      } else if (isZoomOut) {
        event.preventDefault();
        handleZoomOut();
      } else if (isZoomReset) {
        event.preventDefault();
        handleResetZoom();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, handleZoomIn, handleZoomOut, handleResetZoom]);

  useEffect(() => {
    const bodyEl = bodyRef.current;
    if (!bodyEl) return;

    const handleWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.1 : 0.9;
        setZoom((prev) => {
          const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, prev * factor));
          return Math.round(next * 100) / 100;
        });
      }
    };

    bodyEl.addEventListener("wheel", handleWheel, { passive: false });
    return () => {
      bodyEl.removeEventListener("wheel", handleWheel);
    };
  }, []);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest("button, a, input")) return;
    dragStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      scrollLeft: bodyRef.current ? bodyRef.current.scrollLeft : 0,
      scrollTop: bodyRef.current ? bodyRef.current.scrollTop : 0,
    };
    setIsDragging(true);
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragStartRef.current || !bodyRef.current) return;
    const dx = e.clientX - dragStartRef.current.x;
    const dy = e.clientY - dragStartRef.current.y;
    bodyRef.current.scrollLeft = dragStartRef.current.scrollLeft - dx;
    bodyRef.current.scrollTop = dragStartRef.current.scrollTop - dy;
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (dragStartRef.current) {
      dragStartRef.current = null;
      setIsDragging(false);
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {}
    }
  };

  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      void navigator.clipboard.writeText(code).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  };

  const handleDownload = () => {
    downloadSvg(svg);
  };

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      className="mermaid-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Mermaid 架构图全屏预览"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="mermaid-modal-window">
        <div className="mermaid-modal-header">
          <span className="mermaid-badge">Mermaid 预览</span>
          <div className="mermaid-modal-zoom-bar" role="toolbar" aria-label="缩放控制">
            <Tooltip content="缩小" shortcut="Cmd -">
              <button
                type="button"
                className="code-copy-btn mermaid-zoom-btn"
                onClick={handleZoomOut}
                disabled={zoom <= ZOOM_MIN}
                aria-label="缩小"
                title="缩小 (- / Cmd -)"
              >
                <ZoomOutIcon className="copy-icon-svg" />
              </button>
            </Tooltip>
            <Tooltip content="重置缩放为 100%" shortcut="Cmd 0">
              <button
                type="button"
                className="mermaid-zoom-indicator"
                onClick={handleResetZoom}
                aria-label={`当前缩放 ${Math.round(zoom * 100)}%，点击重置为 100%`}
                title="重置缩放为 100% (0 / Cmd 0)"
              >
                {Math.round(zoom * 100)}%
              </button>
            </Tooltip>
            <Tooltip content="放大" shortcut="Cmd +">
              <button
                type="button"
                className="code-copy-btn mermaid-zoom-btn"
                onClick={handleZoomIn}
                disabled={zoom >= ZOOM_MAX}
                aria-label="放大"
                title="放大 (+ / Cmd +)"
              >
                <ZoomInIcon className="copy-icon-svg" />
              </button>
            </Tooltip>
            <Tooltip content="重置为 100%">
              <button
                type="button"
                className="code-copy-btn mermaid-zoom-btn"
                onClick={handleResetZoom}
                disabled={zoom === ZOOM_DEFAULT}
                aria-label="重置缩放"
                title="重置为 100% (0 / Cmd 0)"
              >
                <ResetZoomIcon className="copy-icon-svg" />
              </button>
            </Tooltip>
          </div>
          <div className="mermaid-actions">
            <Tooltip content="下载图像">
              <button
                type="button"
                className="code-copy-btn"
                onClick={handleDownload}
                aria-label="下载图像"
                title="下载图像"
              >
                <DownloadIcon className="copy-icon-svg" />
              </button>
            </Tooltip>
            <Tooltip content={copied ? "已复制代码" : "复制为代码"}>
              <button
                type="button"
                className="code-copy-btn"
                onClick={handleCopy}
                aria-label={copied ? "已复制代码" : "复制为代码"}
                title={copied ? "已复制代码" : "复制为代码"}
              >
                {copied ? (
                  <CheckmarkIcon className="copy-icon-svg copy-icon-svg--copied" />
                ) : (
                  <CopyIcon className="copy-icon-svg" />
                )}
              </button>
            </Tooltip>
            <Tooltip content="关闭预览" shortcut="Esc">
              <button
                type="button"
                className="code-copy-btn"
                onClick={onClose}
                aria-label="关闭预览"
                title="关闭预览 (Esc)"
              >
                <CloseIcon className="copy-icon-svg" />
              </button>
            </Tooltip>
          </div>
        </div>
        <div
          ref={bodyRef}
          className={`mermaid-modal-body ${isDragging ? "is-dragging" : ""}`}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          <div
            className="mermaid-modal-svg-wrapper"
            style={{ zoom }}
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </div>
      </div>
    </div>,
    document.body,
  );
}

export const MermaidBlock = memo(function MermaidBlock({ code }: { code: string }) {
  const rawId = useId();
  const id = rawId.replace(/[^a-zA-Z0-9_-]/g, "_");
  const [svg, setSvg] = useState<string | undefined>(() => mermaidSvgCache.get(code));
  const [error, setError] = useState<string>();
  const [copied, setCopied] = useState(false);
  const [isModalOpen, setIsModalOpen] = useState(false);

  useEffect(() => {
    const cached = mermaidSvgCache.get(code);
    if (cached) {
      setSvg(cached);
      setError(undefined);
      return;
    }

    if (!mermaidInitialized) {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "loose",
        theme: "neutral",
        fontFamily: "var(--font-ui)",
        suppressErrorRendering: true,
        flowchart: {
          rankSpacing: 28,
          nodeSpacing: 24,
          padding: 8,
          curve: "basis",
          useMaxWidth: false,
        },
        sequence: { useMaxWidth: false },
        gitGraph: { useMaxWidth: false },
        state: { useMaxWidth: false },
        class: { useMaxWidth: false },
        er: { useMaxWidth: false },
        themeVariables: {
          fontSize: "13px",
          edgeLabelBackground: "transparent",
        },
      });
      mermaidInitialized = true;
    }
    let active = true;
    setError(undefined);
    const containerId = `mermaid_${id}`;

    async function renderChart() {
      try {
        const isValid = await mermaid.parse(code, { suppressErrors: true });
        if (!isValid) {
          if (active) setError("图表语法有误或正在生成中");
          cleanStrayMermaidElements(containerId);
          return;
        }

        const { svg: renderedSvg } = await mermaid.render(containerId, code);
        mermaidSvgCache.set(code, renderedSvg);
        if (active) {
          setSvg(renderedSvg);
          setError(undefined);
        }
      } catch (err) {
        if (active) setError(String(err));
        cleanStrayMermaidElements(containerId);
      }
    }

    void renderChart();

    return () => {
      active = false;
      cleanStrayMermaidElements(containerId);
    };
  }, [id, code]);

  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      void navigator.clipboard.writeText(code).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  };

  const handleDownload = () => {
    if (svg) {
      downloadSvg(svg);
    }
  };

  return (
    <>
      <div className="mermaid-container" role="region" aria-label="Mermaid 图表">
        {svg && (
          <div className="mermaid-floating-actions" role="toolbar" aria-label="图表操作">
            <DropdownMenu
              className="mermaid-dropdown"
              trigger={<MoreHorizontalIcon className="mermaid-action-icon" />}
              triggerAriaLabel="图表选项"
              title="图表选项"
              label="图表选项"
              items={[
                {
                  key: "copy-code",
                  label: copied ? "已复制代码" : "复制为代码",
                  icon: copied ? <CheckmarkIcon /> : <CodeIcon />,
                  onClick: handleCopy,
                },
                {
                  key: "download-svg",
                  label: "下载图像",
                  icon: <DownloadIcon />,
                  onClick: handleDownload,
                },
              ]}
            />
            <Tooltip content="全屏放大查看">
              <button
                type="button"
                className="mermaid-action-btn"
                onClick={() => setIsModalOpen(true)}
                aria-label="全屏放大查看"
                title="全屏放大查看"
              >
                <ExpandIcon className="mermaid-action-icon" />
              </button>
            </Tooltip>
          </div>
        )}
        {svg ? (
          <div className="mermaid-svg-wrapper" dangerouslySetInnerHTML={{ __html: svg }} />
        ) : (
          <div className="mermaid-fallback-box">
            <pre className="mermaid-fallback">
              <code className="language-mermaid">{code}</code>
            </pre>
            {error && <span className="mermaid-error-hint">图表语法有误或正在生成中</span>}
          </div>
        )}
      </div>
      {isModalOpen && svg && (
        <MermaidModal svg={svg} code={code} onClose={() => setIsModalOpen(false)} />
      )}
    </>
  );
});

