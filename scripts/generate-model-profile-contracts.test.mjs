import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { generateModelProfileTypes } from "./generate-model-profile-contracts.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runtimePath = path.join(projectRoot, "runtime");

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    execFile(command, args, {
      cwd: projectRoot,
      env: {
        ...process.env,
        PYTHONPATH: [runtimePath, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
      },
      ...options,
    }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(`${command} ${args.join(" ")} failed:\n${stderr}`));
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

test("generates closed Model Profile TypeScript contracts from the exported schema", async () => {
  const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-model-contract-"));
  const schemaPath = path.join(temporaryDirectory, "model-profile.schema.json");
  const typePath = path.join(temporaryDirectory, "model-profile.ts");

  try {
    await run("uv", [
      "run", "--locked", "python", "-m", "eidos_runtime.contracts.export_model_profile",
      "--output", schemaPath,
    ]);
    await generateModelProfileTypes({ schemaPath, outputPath: typePath });
    const generated = await readFile(typePath, "utf8");

    assert.match(generated, /Generated from Eidos Runtime Pydantic models/);
    assert.match(generated, /export interface ModelProfile/);
    assert.match(generated, /export interface CapabilitySnapshot/);
    assert.match(generated, /export interface RetryPolicy/);
    assert.match(generated, /supportsTools/);
    assert.doesNotMatch(generated, /supports_tools/);
    assert.doesNotMatch(generated, /\bany\b/);
    assert.doesNotMatch(generated, /\[k:\s*string\]:\s*unknown/);

    const fixturePath = path.join(temporaryDirectory, "fixture.ts");
    const fixture = JSON.parse(await readFile(
      path.join(projectRoot, "contracts", "fixtures", "model-profile.json"),
      "utf8",
    ));
    await writeFile(fixturePath, [
      'import type { ModelProfile } from "./model-profile.js";',
      "",
      `const profile: ModelProfile = ${JSON.stringify(fixture, null, 2)};`,
      "void profile;",
      "",
      "// @ts-expect-error ModelProfile requires its identity fields.",
      "const missingRequired: ModelProfile = { name: \"missing\" };",
      "void missingRequired;",
      "",
      "// @ts-expect-error Unknown fields are rejected for an object literal.",
      "const unknownField: ModelProfile = { ...profile, unexpected: true };",
      "void unknownField;",
      "",
      "// @ts-expect-error Generated enum unions reject unsupported wire APIs.",
      "const invalidEnum: ModelProfile = { ...profile, wireApi: \"unsupported\" };",
      "void invalidEnum;",
      "",
    ].join("\n"), "utf8");
    await run(path.join(projectRoot, "node_modules", ".bin", "tsc"), [
      "--noEmit", "--strict", "--module", "NodeNext", "--moduleResolution", "NodeNext",
      "--target", "ES2023", typePath, fixturePath,
    ]);
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
});
