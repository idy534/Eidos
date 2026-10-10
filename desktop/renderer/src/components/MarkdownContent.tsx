import { memo, useMemo, type ComponentProps } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { ArtifactImage, ArtifactLink, artifactPath, useArtifacts } from "./ArtifactContext.js";
import { CodeBlock } from "./CodeBlock.js";
import { getOfficialFileIcon } from "./FileIcons.js";

export const COMMON_EXTENSIONS = new Set([
  "py", "pyi", "pyx", "ts", "tsx", "js", "jsx", "mjs", "cjs", "rs", "go",
  "c", "cpp", "cc", "cxx", "h", "hpp", "java", "kt", "swift", "html",
  "htm", "css", "scss", "sass", "less", "vue", "svelte", "json", "jsonc",
  "yaml", "yml", "toml", "xml", "sql", "sh", "bash", "zsh", "md", "mdx",
  "txt", "diff", "patch", "svg",
]);

interface HastTextNode {
  type: "text";
  value: string;
}

interface HastElementNode {
  type: "element";
  tagName: string;
  properties?: Record<string, unknown>;
  children: HastNode[];
}

type HastNode = HastTextNode | HastElementNode | { type: string; [key: string]: unknown };

function isMathElement(node: HastElementNode): boolean {
  if (node.tagName === "math" || node.tagName === "annotation") return true;
  const className = node.properties?.className;
  if (Array.isArray(className)) {
    return className.some((name) => typeof name === "string" && name.startsWith("katex"));
  }
  if (typeof className === "string") {
    return className.includes("katex");
  }
  return false;
}

const TOKEN_SPLIT_REGEX = /(\s+|[a-zA-Z0-9_-]+|[^\s\w])/gu;

function splitTokens(text: string): HastNode[] {
  if (!text) return [];
  const parts = text.match(TOKEN_SPLIT_REGEX);
  if (!parts) return [{ type: "text", value: text }];

  const nodes: HastNode[] = [];
  for (let index = 0; index < parts.length; index++) {
    const part = parts[index]!;
    if (!part.trim()) {
      nodes.push({ type: "text", value: part });
    } else {
      nodes.push({
        type: "element",
        tagName: "span",
        properties: { className: ["streaming-token-fade"] },
        children: [{ type: "text", value: part }],
      });
    }
  }
  return nodes;
}

function rehypeStreamingFade() {
  return (tree: { children?: HastNode[] }) => {
    let lastTextNode: HastTextNode | null = null;
    function findLastText(node: HastNode): void {
      if (!("children" in node) || !Array.isArray(node.children)) return;
      for (let i = node.children.length - 1; i >= 0; i--) {
        const child = node.children[i];
        if (
          child
          && child.type === "text"
          && typeof (child as HastTextNode).value === "string"
          && (child as HastTextNode).value.trim().length > 0
        ) {
          lastTextNode = child as HastTextNode;
          return;
        }
        if (child && child.type === "element" && "tagName" in child) {
          const element = child as HastElementNode;
          if (
            element.tagName !== "pre"
            && element.tagName !== "code"
            && element.tagName !== "svg"
            && !isMathElement(element)
          ) {
            findLastText(child);
            if (lastTextNode) return;
          }
        }
      }
    }
    findLastText(tree as HastNode);

    if (lastTextNode && (lastTextNode as HastTextNode).value) {
      const tokens = splitTokens((lastTextNode as HastTextNode).value);
      const target = lastTextNode as unknown as HastElementNode;
      target.type = "element";
      target.tagName = "span";
      target.properties = {};
      target.children = tokens;
    }
  };
}

type MarkdownRehypePlugins = NonNullable<ComponentProps<typeof Markdown>["rehypePlugins"]>;
type MarkdownRemarkPlugins = NonNullable<ComponentProps<typeof Markdown>["remarkPlugins"]>;
const KATEX_REHYPE_OPTIONS = { throwOnError: false, strict: false };
const BASE_REHYPE_PLUGINS: MarkdownRehypePlugins = [
  [rehypeKatex, KATEX_REHYPE_OPTIONS] as unknown as MarkdownRehypePlugins[number],
];
const STREAMING_REHYPE_PLUGINS: MarkdownRehypePlugins = [
  [rehypeKatex, KATEX_REHYPE_OPTIONS] as unknown as MarkdownRehypePlugins[number],
  rehypeStreamingFade as unknown as MarkdownRehypePlugins[number],
];
const REMARK_PLUGINS: MarkdownRemarkPlugins = [remarkGfm, remarkMath];

export const MarkdownContent = memo(function MarkdownContent({
  content,
  documentPath,
  isStreaming = false,
}: {
  content: string;
  documentPath?: string;
  isStreaming?: boolean;
}) {
  const actions = useArtifacts();
  const remarkPlugins = useMemo(() => REMARK_PLUGINS, []);
  const rehypePlugins = useMemo(
    () => (isStreaming ? STREAMING_REHYPE_PLUGINS : BASE_REHYPE_PLUGINS),
    [isStreaming],
  );

  const components = useMemo<ComponentProps<typeof Markdown>["components"]>(() => ({
    a: ({ children, href }) => <ArtifactLink {...(href ? { href } : {})} {...(documentPath ? { documentPath } : {})}>{children}</ArtifactLink>,
    img: ({ alt, src }) => {
      const path = actions && typeof src === "string" ? artifactPath(src, actions.executionRoot, documentPath) : undefined;
      return path ? <ArtifactImage path={path} alt={alt || "图片"} /> : <span className="markdown-image-alt">{alt || "图片"}</span>;
    },
    pre: ({ children }) => <>{children}</>,
    code: ({ className, children, node, ...props }) => {
      const isBlock = Boolean(className) || String(children ?? "").includes("\n");
      if (isBlock) {
        return (
          <CodeBlock className={className} isStreaming={isStreaming}>
            {children}
          </CodeBlock>
        );
      }
      const text = String(children ?? "").trim();
      const match = text.match(/^([a-zA-Z0-9_\-./]+\.([a-zA-Z0-9]+))(?:(?::(\d+)(?::\d+)?)|(?:\s*\((?:line\s*)?(\d+)\))|(?:\s*#L(\d+)))?$/i);
      if (match && actions?.executionRoot) {
        const fullPath = match[1]!;
        const ext = match[2]!.toLowerCase();
        const lineNumber = match[3] || match[4] || match[5];
        if (COMMON_EXTENSIONS.has(ext)) {
          const cleanPath = artifactPath(fullPath, actions.executionRoot, documentPath);
          if (cleanPath) {
            const icon = getOfficialFileIcon(cleanPath || fullPath || ext);
            const displayText = lineNumber ? `${fullPath} (line ${lineNumber})` : fullPath;
            return (
              <span
                role="button"
                tabIndex={0}
                className="markdown-file-link"
                onClick={() => actions.openFile(cleanPath)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    actions.openFile(cleanPath);
                  }
                }}
                title={`在工作区中打开 ${cleanPath}${lineNumber ? ` (第 ${lineNumber} 行)` : ""}`}
              >
                <span className="markdown-file-icon" aria-hidden="true">{icon}</span>
                <span className="markdown-file-text">{displayText}</span>
              </span>
            );
          }
        }
      }
      return <code className={className}>{children}</code>;
    },
  }), [actions, documentPath, isStreaming]);

  return (
    <div className="markdown-body">
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        skipHtml
        urlTransform={(url) => /^(?:javascript|vbscript|data):/i.test(url) ? "" : url}
        components={components}
      >
        {content}
      </Markdown>
    </div>
  );
});
