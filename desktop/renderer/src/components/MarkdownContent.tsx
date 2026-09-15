import type { ComponentProps } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArtifactImage, ArtifactLink, artifactPath, useArtifacts } from "./ArtifactContext.js";

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
          if (child.tagName !== "pre" && child.tagName !== "code" && child.tagName !== "svg") {
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
const EMPTY_REHYPE_PLUGINS: MarkdownRehypePlugins = [];
const STREAMING_REHYPE_PLUGINS: MarkdownRehypePlugins = [rehypeStreamingFade as unknown as MarkdownRehypePlugins[number]];

export function MarkdownContent({
  content,
  documentPath,
  isStreaming = false,
}: {
  content: string;
  documentPath?: string;
  isStreaming?: boolean;
}) {
  const actions = useArtifacts();
  const rehypePlugins = isStreaming ? STREAMING_REHYPE_PLUGINS : EMPTY_REHYPE_PLUGINS;

  return (
    <div className="markdown-body">
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={rehypePlugins}
        skipHtml
        urlTransform={(url) => /^(?:javascript|vbscript|data):/i.test(url) ? "" : url}
        components={{
          a: ({ children, href }) => <ArtifactLink {...(href ? { href } : {})} {...(documentPath ? { documentPath } : {})}>{children}</ArtifactLink>,
          img: ({ alt, src }) => {
            const path = actions && typeof src === "string" ? artifactPath(src, actions.executionRoot, documentPath) : undefined;
            return path ? <ArtifactImage path={path} alt={alt || "图片"} /> : <span className="markdown-image-alt">{alt || "图片"}</span>;
          },
        }}>
        {content}
      </Markdown>
    </div>
  );
}
