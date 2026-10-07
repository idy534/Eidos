import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { failedCiChecks } from "./ci-result.mjs";
import { selectCiScopes } from "./ci-scope.mjs";

test("documentation changes retain independent quality/security without irrelevant suites", () => {
  assert.deepEqual(selectCiScopes(["docs/current-execution-gates.md"]), { runtime: false, desktop: false, native: false });
});
test("renderer, shared contracts, runtime implementation and runtime tests select their actual boundaries", () => {
  assert.deepEqual(selectCiScopes(["desktop/renderer/src/components/Composer.tsx"]), { runtime: false, desktop: true, native: false });
  assert.deepEqual(selectCiScopes(["desktop/shared/collaboration.ts"]), { runtime: true, desktop: true, native: true });
  assert.deepEqual(selectCiScopes(["runtime/eidos_runtime/runtime/engine.py"]), { runtime: true, desktop: false, native: true });
  assert.deepEqual(selectCiScopes(["runtime/tests/test_collaboration.py"]), { runtime: true, desktop: false, native: false });
  assert.deepEqual(selectCiScopes(["runtime/eidos_runtime/protocol/schemas.py"]), { runtime: true, desktop: true, native: true });
});
test("lockfiles, CI, scripts and main pushes require all boundaries", () => {
  for (const file of ["uv.lock", "pnpm-lock.yaml", ".github/workflows/ci.yml", "scripts/ci-scope.mjs"])
    assert.deepEqual(selectCiScopes([file]), { runtime: true, desktop: true, native: true });
  assert.deepEqual(selectCiScopes([], true), { runtime: true, desktop: true, native: true });
});
test("a cross-boundary rename still validates the removed implementation", () => {
  const directory = mkdtempSync(join(tmpdir(), "eidos-ci-scope-"));
  const git = (...args) => execFileSync("git", args, { cwd: directory, encoding: "utf8" }).trim();
  try {
    git("init", "-q");
    git("config", "user.name", "Scope fixture");
    git("config", "user.email", "scope@example.invalid");
    execFileSync("mkdir", ["-p", join(directory, "runtime"), join(directory, "docs")]);
    writeFileSync(join(directory, "runtime/implementation.py"), "pass\n");
    git("add", "."); git("commit", "-qm", "Before");
    const before = git("rev-parse", "HEAD");
    git("mv", "runtime/implementation.py", "docs/archive.py");
    git("commit", "-qm", "Move");
    const output = execFileSync(process.execPath, [resolve("scripts/ci-scope.mjs"), before, git("rev-parse", "HEAD")], { cwd: directory, encoding: "utf8", env: { ...process.env, GITHUB_EVENT_NAME: "pull_request", GITHUB_OUTPUT: "" } });
    assert.match(output, /^runtime=true$/m);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

function checks(selected) {
  return {
    changes: { result: "success", outputs: { runtime: String(selected), desktop: String(selected), native: String(selected) } },
    quality: { result: "success" }, security: { result: "success" },
    runtime: { result: selected ? "success" : "skipped" },
    desktop: { result: selected ? "success" : "skipped" },
    native: { result: selected ? "success" : "skipped" },
  };
}
test("CI only accepts skipped suites when change detection explicitly deselects them", () => {
  assert.deepEqual(failedCiChecks(checks(false)), []);
  assert.deepEqual(failedCiChecks(checks(true)), []);
  const selected = checks(true); selected.runtime.result = "skipped";
  assert.deepEqual(failedCiChecks(selected), ["runtime"]);
  const missing = checks(false); delete missing.changes.outputs.native;
  assert.deepEqual(failedCiChecks(missing), ["native"]);
});
test("CI requires successful quality, security and detection even when suites are deselected", () => {
  for (const name of ["changes", "quality", "security"])
    for (const result of ["skipped", "failure", "cancelled"]) {
      const input = checks(false); input[name].result = result;
      assert.ok(failedCiChecks(input).includes(name));
    }
  assert.equal(failedCiChecks({}).length, 6);
  assert.deepEqual(failedCiChecks({ ...checks(false), extra: { result: "failure" } }), ["extra"]);
});
