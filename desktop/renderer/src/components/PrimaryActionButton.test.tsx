import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import { PrimaryActionButton } from "./PrimaryActionButton.js";

test("renders compact size PrimaryActionButton with label and shortcut", () => {
  const html = renderToStaticMarkup(
    <PrimaryActionButton
      size="compact"
      label="新建任务"
      shortcut="⌘N"
    />,
  );

  assert.match(html, /class="primary-action-btn primary-action-btn--compact"/);
  assert.match(html, /新建任务/);
  assert.match(html, /<span class="primary-action-shortcut" aria-hidden="true">⌘N<\/span>/);
  assert.match(html, /class="primary-action-icon"/);
});

test("renders large size PrimaryActionButton with title, subtitle, and arrow", () => {
  const html = renderToStaticMarkup(
    <PrimaryActionButton
      size="large"
      label="选择工作空间目录"
      subtitle="打开一个本地项目开始使用 Eidos"
      showArrow={true}
    />,
  );

  assert.match(html, /class="primary-action-btn primary-action-btn--large"/);
  assert.match(html, /选择工作空间目录/);
  assert.match(html, /打开一个本地项目开始使用 Eidos/);
  assert.match(html, /class="primary-action-arrow"/);
});

test("handles disabled and loading states cleanly", () => {
  const disabledHtml = renderToStaticMarkup(
    <PrimaryActionButton
      size="compact"
      label="新建任务"
      disabled={true}
    />,
  );
  assert.match(disabledHtml, /disabled=""/);

  const loadingHtml = renderToStaticMarkup(
    <PrimaryActionButton
      size="compact"
      label="新建任务"
      loading={true}
      loadingText="正在创建…"
    />,
  );

  assert.match(loadingHtml, /aria-busy="true"/);
  assert.match(loadingHtml, /正在创建…/);
  assert.match(loadingHtml, /class="primary-action-spinner"/);
});
