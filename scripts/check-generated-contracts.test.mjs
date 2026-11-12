import assert from "node:assert/strict";
import test from "node:test";
import { compareSnapshots } from "./check-generated-contracts.mjs";

const file = (text, modified) => ({ text, modified });

test("regenerated files with the same text are not reported", () => {
  const before = new Map([["a.generated.ts", file("same", 1n)]]);
  const after = new Map([["a.generated.ts", file("same", 2n)]]);
  assert.deepEqual(compareSnapshots(before, after), { changed: [], added: [], orphaned: [] });
});

test("a file whose regenerated text differs is drift", () => {
  const before = new Map([["a.generated.ts", file("old", 1n)]]);
  const after = new Map([["a.generated.ts", file("new", 2n)]]);
  assert.deepEqual(compareSnapshots(before, after).changed, ["a.generated.ts"]);
});

test("a generator output that was never committed is reported", () => {
  const after = new Map([["b.generated.ts", file("new", 2n)]]);
  assert.deepEqual(compareSnapshots(new Map(), after).added, ["b.generated.ts"]);
});

test("a committed file that no generator rewrote is orphaned", () => {
  const before = new Map([["a.generated.ts", file("kept", 1n)]]);
  const after = new Map([["a.generated.ts", file("kept", 1n)]]);
  assert.deepEqual(compareSnapshots(before, after).orphaned, ["a.generated.ts"]);
});
