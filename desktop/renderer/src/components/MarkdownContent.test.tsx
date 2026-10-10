import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import { MarkdownContent } from "./MarkdownContent.js";
import { ArtifactProvider } from "./ArtifactContext.js";
import { FileIcon, resolveIconPath } from "./FileIcons.js";


test("renders assistant markdown as semantic content", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"# 结论\n\n- 第一项\n- 第二项\n\n```ts\nconst ready = true;\n```"} />,
  );

  assert.match(html, /<h1>结论<\/h1>/);
  assert.match(html, /<ul>/);
  assert.match(html, /<li>第一项<\/li>/);
  assert.match(html, /<pre><code class="language-ts">/);
  assert.match(html, /const ready = true;/);
});

test("renders GFM tables as semantic table content", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"| 顺序 | 项目 |\n| --- | --- |\n| 1 | 开窗通风 |"} />,
  );

  assert.match(html, /<table>/);
  assert.match(html, /<th>顺序<\/th>/);
  assert.match(html, /<td>开窗通风<\/td>/);
});

test("does not activate raw HTML, remote images, or links", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"<script>alert('xss')</script>\n\n![远程图](https://example.com/a.png)\n\n[文档](https://example.com)"} />,
  );

  assert.doesNotMatch(html, /<script|<img|<a\b/);
  assert.match(html, /远程图/);
  assert.match(html, /文档/);
});

test("renders streaming tokens with fade spans when isStreaming is true", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"你好世界 Hello world"} isStreaming={true} />,
  );

  assert.match(html, /<span class="streaming-token-fade">你<\/span>/);
  assert.match(html, /<span class="streaming-token-fade">好<\/span>/);
  assert.match(html, /<span class="streaming-token-fade">世<\/span>/);
  assert.match(html, /<span class="streaming-token-fade">界<\/span>/);
  assert.match(html, /<span class="streaming-token-fade">Hello<\/span>/);
  assert.match(html, /<span class="streaming-token-fade">world<\/span>/);
});

test("renders static semantic content without any streaming spans when isStreaming is false", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"你好世界 Hello world"} isStreaming={false} />,
  );

  assert.doesNotMatch(html, /streaming-token-fade/);
  assert.equal(html, '<div class="markdown-body"><p>你好世界 Hello world</p></div>');
});

test("does not wrap pre or code blocks in streaming spans during streaming", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"```ts\nconst x = 1;\n```"} isStreaming={true} />,
  );

  assert.doesNotMatch(html, /streaming-token-fade/);
  assert.match(html, /<pre><code class="language-ts">/);
  assert.match(html, /const x = 1;/);
});

test("renders inline math formula via KaTeX", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"质能方程是 $E = mc^2$。"} />,
  );

  assert.match(html, /<span class="katex">/);
  assert.match(html, /<annotation encoding="application\/x-tex">E = mc\^2<\/annotation>/);
});

test("renders multiline display math formula via KaTeX block layout", () => {
  const formula = "$$\n\\int_{-\\infty}^{+\\infty} e^{-x^2} \\, dx = \\sqrt{\\pi}\n$$";
  const html = renderToStaticMarkup(
    <MarkdownContent content={`高斯积分：\n\n${formula}`} />,
  );

  assert.match(html, /<span class="katex-display">/);
  assert.match(html, /<span class="katex">/);
  assert.match(html, /<annotation encoding="application\/x-tex">\\int_\{-\\infty\}\^\{\+\\infty\} e\^\{-x\^2\} \\, dx = \\sqrt\{\\pi\}<\/annotation>/);
});

test("protects KaTeX math nodes from streaming-token-fade wrapping during streaming", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"公式：\n\n$$\nE = mc^2\n$$"} isStreaming={true} />,
  );

  assert.match(html, /<span class="katex-display">/);
  assert.doesNotMatch(html, /class="katex[^"]*streaming-token-fade/);
  assert.doesNotMatch(html, /<span class="streaming-token-fade">E<\/span>/);
});

test("handles malformed LaTeX formula gracefully without throwing", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"错误公式：$\\invalidcommand{foo$"} />,
  );

  assert.match(html, /katex-error/);
});

test("renders code block with Chrome header (language label and icon copy button)", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"```typescript\nconst x = 1;\n```"} />,
  );

  assert.match(html, /class="code-block-card"/);
  assert.match(html, /class="code-block-language">TypeScript<\/span>/);
  assert.match(html, /class="code-copy-btn"[^>]*aria-label="复制代码"/);
  assert.match(html, /class="copy-icon-svg"/);
});

test("renders diff code block via DiffViewer with colored line annotations and file stats", () => {
  const diff = "```diff\n--- a/file.ts\n+++ b/file.ts\n@@ -1 +1 @@\n-old\n+new\n```";
  const html = renderToStaticMarkup(
    <MarkdownContent content={diff} />,
  );

  assert.match(html, /class="diff-viewer-card/);
  assert.match(html, /class="diff-file-name">file\.ts<\/span>/);
  assert.match(html, /class="diff-stat-add">\+1<\/span>/);
  assert.match(html, /class="diff-stat-del">-1<\/span>/);
  assert.match(html, /class="diff-line diff-line--add\b/);
  assert.match(html, /class="diff-line diff-line--del\b/);
  assert.match(html, /class="diff-line diff-line--hunk\b/);
});

test("renders multi-file diff correctly with multiple cards", () => {
  const multiDiff = `\`\`\`diff
diff --git a/mcp_fixture.py b/mcp_fixture.py
--- a/mcp_fixture.py
+++ b/mcp_fixture.py
@@ -75,2 +75,3 @@
 context
+added
-deleted
diff --git a/test_mcp.py b/test_mcp.py
--- a/test_mcp.py
+++ b/test_mcp.py
@@ -10,1 +10,2 @@
+test_added
\`\`\``;
  const html = renderToStaticMarkup(<MarkdownContent content={multiDiff} />);

  assert.match(html, /class="diff-file-name">mcp_fixture\.py<\/span>/);
  assert.match(html, /class="diff-file-name">test_mcp\.py<\/span>/);
  assert.match(html, /class="code-copy-btn"/);
});

test("renders mermaid diagram block via MermaidBlock container with icon copy button", () => {
  const mermaid = "```mermaid\ngraph TD\nA --> B\n```";
  const html = renderToStaticMarkup(
    <MarkdownContent content={mermaid} />,
  );

  assert.match(html, /class="mermaid-container"/);
  assert.match(html, /<span class="mermaid-badge">Mermaid<\/span>/);
  assert.match(html, /class="language-mermaid"/);
  assert.match(html, /class="code-copy-btn"[^>]*aria-label="复制代码"/);
});

test("renders workspace file path in inline code as clickable file link with official extension icon and line number", () => {
  const mockActions = {
    sessionId: "s-1",
    executionRoot: "/workspace",
    openFile: () => {},
    openBrowser: () => {},
  };
  const html = renderToStaticMarkup(
    <ArtifactProvider value={mockActions}>
      <MarkdownContent content={"测试改动位于 `test_mcp.py (line 456)` 和 `src/app/App.tsx:42` 文件。"} />
    </ArtifactProvider>,
  );

  assert.match(html, /class="markdown-file-link"/);
  assert.match(html, /class="file-icon-svg"/);
  assert.match(html, /fill="#0288d1"/);
  assert.match(html, /class="markdown-file-text">test_mcp\.py \(line 456\)<\/span>/);
  assert.match(html, /fill="#5bcbdc"/);
  assert.match(html, /class="markdown-file-text">src\/app\/App\.tsx \(line 42\)<\/span>/);
});

test("renders unknown workspace files with unknown.svg silhouette icon", () => {
  assert.equal(resolveIconPath("unknown_config.xyz"), "icons/system/unknown.svg");
  assert.equal(resolveIconPath("some_file_without_extension"), "icons/system/unknown.svg");

  const html = renderToStaticMarkup(<FileIcon name="unknown_config.xyz" />);
  assert.match(html, /class="file-icon-svg"/);
  assert.match(html, /fill="#8b96a7"/);
  assert.match(html, /viewBox="0 0 384 512"/);
});

test("resolves special filenames and compound extensions from manifest", () => {
  assert.equal(resolveIconPath("Dockerfile"), "icons/code/docker.svg");
  assert.equal(resolveIconPath("package.json"), "icons/code/npm.svg");
  assert.equal(resolveIconPath("go.mod"), "icons/code/go.svg");
  assert.equal(resolveIconPath("App.test.tsx"), "icons/code/react.svg");
  assert.equal(resolveIconPath("types.d.ts"), "icons/code/typescript.svg");
  assert.equal(resolveIconPath("archive.tar.gz"), "icons/archive/archive.svg");
  assert.equal(resolveIconPath("src/main.rs"), "icons/code/rust.svg");
});

test("renders apply_patch multi-file diff with individual stats and metadata line filtering", () => {
  const patchDiff = `\`\`\`diff
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello
+hello world
\\ Eidos EOF newline: before=absent, after=present
\\ Eidos line endings: before=none, after=LF:2
--- a/notes.md
+++ b/notes.md
@@ -1 +1,2 @@
 # Notes
+Added note
\`\`\``;
  const html = renderToStaticMarkup(<MarkdownContent content={patchDiff} />);
  assert.match(html, /class="diff-file-name">hello\.txt<\/span>/);
  assert.match(html, /class="diff-file-name">notes\.md<\/span>/);
  assert.match(html, /class="diff-stat-add">\+1<\/span>/);
  assert.doesNotMatch(html, /Eidos EOF newline/);
  assert.doesNotMatch(html, /Eidos line endings/);
});

test("formats language names in Title case matching UI specification", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"```go\npackage main\n```"} />,
  );
  assert.match(html, /class="code-block-language">Go<\/span>/);
});
