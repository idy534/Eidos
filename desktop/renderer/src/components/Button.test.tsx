import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { Button } from "./Button.js";

void test("Button renders with default type=button, size=medium, variant=secondary", () => {
  const html = renderToStaticMarkup(<Button>Click Me</Button>);
  assert.match(html, /<button [^>]*type="button"/);
  assert.match(html, /class="[^"]*btn/);
  assert.match(html, /class="[^"]*btn--secondary/);
  assert.match(html, /class="[^"]*btn--medium/);
  assert.match(html, /<span class="btn-label">Click Me<\/span>/);
});

void test("Button loading state disables button and adds aria-busy", () => {
  const html = renderToStaticMarkup(<Button loading>Submit</Button>);
  assert.match(html, /disabled=""/);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /class="[^"]*btn--loading/);
  assert.match(html, /class="btn-spinner"/);
});

void test("Button variants produce distinct class names", () => {
  const primary = renderToStaticMarkup(<Button variant="primary">Save</Button>);
  const secondary = renderToStaticMarkup(<Button variant="secondary">Cancel</Button>);
  const danger = renderToStaticMarkup(<Button variant="danger">Delete</Button>);
  const ghost = renderToStaticMarkup(<Button variant="ghost">Close</Button>);

  assert.match(primary, /btn--primary/);
  assert.match(secondary, /btn--secondary/);
  assert.match(danger, /btn--danger/);
  assert.match(ghost, /btn--ghost/);
  assert.notEqual(secondary, danger);
});

void test("Button supports custom size, icon, and custom className", () => {
  const icon = <svg data-testid="icon" />;
  const html = renderToStaticMarkup(
    <Button size="small" variant="primary" icon={icon} className="my-custom-btn">
      Small Action
    </Button>
  );

  assert.match(html, /btn--small/);
  assert.match(html, /my-custom-btn/);
  assert.match(html, /btn-icon/);
});
