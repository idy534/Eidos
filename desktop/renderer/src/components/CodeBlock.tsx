import { memo, useEffect, useState, type ReactNode } from "react";
import { DiffViewer } from "./DiffViewer.js";
import { MermaidBlock } from "./MermaidBlock.js";
import { CopyIcon, CheckmarkIcon } from "./MenuIcons.js";

const LANGUAGE_LABELS: Record<string, string> = {
  ts: "TypeScript",
  typescript: "TypeScript",
  tsx: "TSX",
  js: "JavaScript",
  javascript: "JavaScript",
  jsx: "JSX",
  py: "Python",
  python: "Python",
  json: "JSON",
  html: "HTML",
  css: "CSS",
  scss: "SCSS",
  sh: "Bash",
  bash: "Bash",
  zsh: "Bash",
  shell: "Bash",
  rust: "Rust",
  rs: "Rust",
  go: "Go",
  sql: "SQL",
  yaml: "YAML",
  yml: "YAML",
  md: "Markdown",
  markdown: "Markdown",
  diff: "Diff",
  mermaid: "Mermaid",
  c: "C",
  cpp: "C++",
  java: "Java",
  kt: "Kotlin",
  swift: "Swift",
  php: "PHP",
  ruby: "Ruby",
  lua: "Lua",
};

function formatLanguage(lang: string): string {
  const normalized = lang.toLowerCase();
  if (LANGUAGE_LABELS[normalized]) {
    return LANGUAGE_LABELS[normalized]!;
  }
  if (!lang) return "Text";
  return lang.charAt(0).toUpperCase() + lang.slice(1).toLowerCase();
}

export const CodeBlock = memo(function CodeBlock({
  className,
  children,
  isStreaming = false,
}: {
  className?: string | undefined;
  children?: ReactNode;
  isStreaming?: boolean | undefined;
}) {
  const match = /language-([a-zA-Z0-9_-]+)/.exec(className || "");
  const lang = match ? match[1]! : "";
  const rawCode = String(children ?? "").replace(/\n$/, "");
  const [shikiHtml, setShikiHtml] = useState<string>();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let active = true;
    if (!rawCode || isStreaming || !lang || lang === "mermaid" || lang === "diff") {
      return;
    }
    void import("shiki/bundle/web")
      .then(async ({ codeToHtml }) => {
        try {
          const rendered = await codeToHtml(rawCode, {
            lang,
            theme: "github-light-default",
          });
          if (active) setShikiHtml(rendered);
        } catch {
          try {
            const fallback = await codeToHtml(rawCode, {
              lang: "text",
              theme: "github-light-default",
            });
            if (active) setShikiHtml(fallback);
          } catch {}
        }
      })
      .catch(() => {});

    return () => {
      active = false;
    };
  }, [rawCode, lang, isStreaming]);

  if (lang.toLowerCase() === "mermaid") {
    return <MermaidBlock code={rawCode} />;
  }

  if (lang.toLowerCase() === "diff") {
    return <DiffViewer diff={rawCode} title="代码差异" />;
  }

  const handleCopy = () => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      void navigator.clipboard.writeText(rawCode).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  };

  return (
    <div className="code-block-card" role="region" aria-label={`${lang || "代码"}片段`}>
      <div className="code-block-header">
        <span className="code-block-language">{formatLanguage(lang || "text")}</span>
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
      {shikiHtml ? (
        <div className="shiki-code-body" dangerouslySetInnerHTML={{ __html: shikiHtml }} />
      ) : (
        <pre>
          <code className={className}>{rawCode}</code>
        </pre>
      )}
    </div>
  );
});
