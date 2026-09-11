import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArtifactImage, ArtifactLink, artifactPath, useArtifacts } from "./ArtifactContext.js";

export function MarkdownContent({ content, documentPath }: { content: string; documentPath?: string }) {
  const actions = useArtifacts();
  return (
    <div className="markdown-body">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={(url) => /^(?:javascript|vbscript|data):/i.test(url) ? "" : url}
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
