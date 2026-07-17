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

test("does not activate raw HTML, remote images, or links", () => {
  const html = renderToStaticMarkup(
    <MarkdownContent content={"<script>alert('xss')</script>\n\n![远程图](https://example.com/a.png)\n\n[文档](https://example.com)"} />,
  );

  assert.doesNotMatch(html, /<script|<img|<a\b/);
  assert.match(html, /远程图/);
  assert.match(html, /文档/);
});
