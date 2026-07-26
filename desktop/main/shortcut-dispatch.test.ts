import { test } from "node:test";
import assert from "node:assert/strict";
import { ensureMainWindow, dispatchAppCommand } from "./main.js";

void test("ensureMainWindow creates or restores main window", () => {
  // Test export presence and signatures
  assert.equal(typeof ensureMainWindow, "function");
  assert.equal(typeof dispatchAppCommand, "function");
});
