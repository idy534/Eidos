import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const checker = path.join(root, "scripts/check-desktop-contracts.mjs");

function rejectsMutation(relativePath, mutate, expectedError) {
  const fixtureRoot = mkdtempSync(path.join(tmpdir(), "eidos-contracts-"));
  try {
    cpSync(path.join(root, "desktop"), path.join(fixtureRoot, "desktop"), {
      recursive: true,
    });
    cpSync(
      path.join(root, "tsconfig.renderer-test.json"),
      path.join(fixtureRoot, "tsconfig.renderer-test.json"),
    );
    const target = path.join(fixtureRoot, relativePath);
    const source = readFileSync(target, "utf8");
    const mutated = mutate(source);
    assert.notEqual(mutated, source, `fixture mutation did not change ${relativePath}`);
    writeFileSync(target, mutated);
    const result = spawnSync(process.execPath, [checker, "--root", fixtureRoot], {
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0, result.stdout);
    assert.match(`${result.stdout}\n${result.stderr}`, expectedError);
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
}

test("Approval projection rejects an extra explicit field", () => {
  rejectsMutation(
    "desktop/main/runtime-client.ts",
    (source) => source.replace(
      "      diff: params.diff as string,\n",
      "      diff: params.diff as string,\n      secret: params.secret,\n",
    ),
    /file_change.*exact fields/,
  );
});

test("Approval clearing rejects the run notification parent branch", () => {
  rejectsMutation(
    "desktop/renderer/src/app/AppShell.tsx",
    (source) => source.replace(
      '        if (notification.method === "run/completed") {\n'
        + "          approvalActions.clearApprovalsForRun(run.id);\n",
      "        approvalActions.clearApprovalsForRun(run.id);\n"
        + '        if (notification.method === "run/completed") {\n',
    ),
    /clearApprovalsForRun.*run\/completed/,
  );
});

test("Approval removal rejects an unconditional notification block", () => {
  rejectsMutation(
    "desktop/renderer/src/app/AppShell.tsx",
    (source) => source
      .replace(
        '      if (notification.method === "session/titleUpdated") {\n',
        "      approvalActions.removeApproval(notification.params.approvalId);\n"
          + '      if (notification.method === "session/titleUpdated") {\n',
      )
      .replace(
        "        approvalActions.removeApproval(notification.params.approvalId);\n",
        "",
      ),
    /removeApproval.*approval\/resolved.*approval\/canceled/,
  );
});
