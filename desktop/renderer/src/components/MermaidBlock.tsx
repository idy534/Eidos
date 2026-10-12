import { memo, useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import mermaid from "mermaid";
import { DropdownMenu } from "./DropdownMenu.js";
import {
  CopyIcon,
  CheckmarkIcon,
  ExpandIcon,
  CloseIcon,
  MoreHorizontalIcon,
  DownloadIcon,
  CodeIcon,
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

function MermaidModal({
  svg,
  code,
  onClose,
}: {
  svg: string;
  code: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

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
          <div className="mermaid-actions">
            <button
              type="button"
              className="code-copy-btn"
              onClick={handleDownload}
              aria-label="下载图像"
              title="下载图像"
            >
              <DownloadIcon className="copy-icon-svg" />
            </button>
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
            <button
              type="button"
              className="code-copy-btn"
              onClick={onClose}
              aria-label="关闭预览"
              title="关闭预览 (Esc)"
            >
              <CloseIcon className="copy-icon-svg" />
            </button>
          </div>
        </div>
        <div className="mermaid-modal-body">
          <div
            className="mermaid-modal-svg-wrapper"
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
            <button
              type="button"
              className="mermaid-action-btn"
              onClick={() => setIsModalOpen(true)}
              aria-label="全屏放大查看"
              title="全屏放大查看"
            >
              <ExpandIcon className="mermaid-action-icon" />
            </button>
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

