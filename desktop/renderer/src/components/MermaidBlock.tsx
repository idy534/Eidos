import { memo, useEffect, useId, useState } from "react";
import mermaid from "mermaid";
import { CopyIcon, CheckmarkIcon } from "./MenuIcons.js";

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

export const MermaidBlock = memo(function MermaidBlock({ code }: { code: string }) {
  const rawId = useId();
  const id = rawId.replace(/[^a-zA-Z0-9_-]/g, "_");
  const [svg, setSvg] = useState<string | undefined>(() => mermaidSvgCache.get(code));
  const [error, setError] = useState<string>();
  const [copied, setCopied] = useState(false);

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
          useMaxWidth: true,
        },
        themeVariables: {
          fontSize: "13px",
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

  return (
    <div className="mermaid-container" role="region" aria-label="Mermaid">
      <div className="mermaid-header">
        <span className="mermaid-badge">Mermaid</span>
        <button
          type="button"
          className="code-copy-btn"
          onClick={handleCopy}
          aria-label={copied ? "已复制代码" : "复制代码"}
          title={copied ? "已复制代码" : "复制代码"}
        >
          {copied ? (
            <CheckmarkIcon className="copy-icon-svg copy-icon-svg--copied" />
          ) : (
            <CopyIcon className="copy-icon-svg" />
          )}
        </button>
      </div>
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
  );
});

