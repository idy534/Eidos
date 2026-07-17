import Markdown from "react-markdown";


export function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <Markdown
        skipHtml
        components={{
          a: ({ children, href }) => <span className="markdown-link" title={href}>{children}</span>,
          img: ({ alt }) => <span className="markdown-image-alt">{alt || "图片"}</span>,
        }}
      >
        {content}
      </Markdown>
    </div>
  );
}
