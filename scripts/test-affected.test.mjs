import assert from "node:assert/strict";
import test from "node:test";

import { selectAffectedCommands } from "./test-affected.mjs";

function commandLines(changedFiles) {
  return selectAffectedCommands(changedFiles).map(({ command, args }) => [command, ...args].join(" "));
}

test("model changes select only the model-focused Runtime tests", () => {
  assert.deepEqual(
    commandLines(["runtime/eidos_runtime/model/gateway.py"]),
    ["uv run --locked pytest runtime/tests -k model or gateway or pydantic_ai"],
  );
});

test("mixed mapped and unmapped Runtime changes also run Runtime Fast", () => {
  assert.deepEqual(
    commandLines([
      "runtime/eidos_runtime/model/gateway.py",
      "runtime/eidos_runtime/runtime/engine.py",
    ]),
    [
      "uv run --locked pytest runtime/tests -k model or gateway or pydantic_ai",
      "pnpm test:runtime:fast",
    ],
  );
});

test("multiple mapped Runtime domains keep all focused selections without Fast fallback", () => {
  assert.deepEqual(
    commandLines([
      "runtime/eidos_runtime/model/gateway.py",
      "runtime/eidos_runtime/context/builder.py",
    ]),
    [
      "uv run --locked pytest runtime/tests -k model or gateway or pydantic_ai",
      "uv run --locked pytest runtime/tests -k context or instruction or project_rule",
    ],
  );
});

test("a directly changed Runtime test is selected by its current path", () => {
  assert.deepEqual(
    commandLines(["runtime/tests/test_run_supervisor.py"]),
    ["uv run --locked pytest runtime/tests/test_run_supervisor.py"],
  );
});

test("test filenames do not trigger production directory mappings", () => {
  assert.deepEqual(
    commandLines(["runtime/tests/test_worktree_lifecycle.py"]),
    ["uv run --locked pytest runtime/tests/test_worktree_lifecycle.py"],
  );
});

test("Desktop renderer changes use the existing desktop verification entrypoint", () => {
  assert.deepEqual(commandLines(["desktop/renderer/Feed.tsx"]), ["pnpm test:desktop"]);
});

test("unknown changes conservatively fall back to Fast instead of Full", () => {
  assert.deepEqual(commandLines(["docs/current-architecture.md"]), ["pnpm test:fast"]);
  assert.ok(!commandLines(["docs/current-architecture.md"]).includes("pnpm test:full"));
});

test("packaging changes select packaging tests without invoking a package build", () => {
  assert.deepEqual(commandLines(["scripts/package-macos.sh"]), ["pnpm test:packaging"]);
});
