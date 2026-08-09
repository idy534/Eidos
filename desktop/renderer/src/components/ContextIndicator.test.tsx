import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { ContextIndicator } from "./ContextIndicator.js";

void test("ContextIndicator renders circular ring and tooltip with formatted context details", () => {
  const html = renderToStaticMarkup(
    <ContextIndicator
      usage={{
        activeTokens: 145_100,
        windowTokens: 802_800,
        percentUsed: 18,
        source: "provider",
      }}
    />
  );
  assert.match(html, /composer-context-usage/);
  assert.match(html, /context-progress-ring/);
  assert.match(html, /context-progress-track/);
  assert.match(html, /context-progress-arc/);
  assert.match(html, /composer-context-tooltip/);
  assert.match(html, /上下文 18% · 145.1K \/ 802.8K/);
});

void test("ContextIndicator renders fallback state when usage is undefined", () => {
  const html = renderToStaticMarkup(<ContextIndicator usage={undefined} />);
  assert.match(html, /composer-context-usage/);
  assert.match(html, /上下文 --/);
});
