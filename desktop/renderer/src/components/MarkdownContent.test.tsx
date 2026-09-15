import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import { MarkdownContent } from "./MarkdownContent.js";


test("renders assistant markdown as semantic content", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"# 结论\n\n- 第一项\n- 第二项\n\n```ts\nconst ready = true;\n```"} />,
  );

  assert.match(html, /<h1>结论<\/h1>/);
  assert.match(html, /<ul>/);
  assert.match(html, /<li>第一项<\/li>/);
  assert.match(html, /<pre><code class="language-ts">const ready = true;/);
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
  assert.match(html, /<pre><code class="language-ts">const x = 1;\n<\/code><\/pre>/);
});
