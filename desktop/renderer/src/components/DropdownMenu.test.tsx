import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { DropdownMenu, ContextMenu } from "./DropdownMenu.js";

void test("DropdownMenu renders closed state with trigger button", () => {
  const items = [
    { key: "item1", label: "Item 1", onClick: () => {} },
    { key: "item2", label: "Item 2", onClick: () => {} },
  ];
  const html = renderToStaticMarkup(<DropdownMenu trigger="Menu" items={items} label="Test Menu" />);

  assert.match(html, /aria-haspopup="menu"/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /Menu/);
  // Menu items are closed by default
  assert.doesNotMatch(html, /role="menu"/);
});

void test("ContextMenu renders portal menu at position (x, y)", () => {
  const items = [
    { key: "rename", label: "Rename Task", onClick: () => {} },
    { key: "delete", label: "Delete Task", danger: true, onClick: () => {} },
  ];
  const html = renderToStaticMarkup(
    <ContextMenu items={items} x={150} y={250} label="Task Menu" onClose={() => {}} />
  );

  assert.match(html, /role="menu"/);
  assert.match(html, /Rename Task/);
  assert.match(html, /Delete Task/);
  assert.match(html, /danger-action/);
});
