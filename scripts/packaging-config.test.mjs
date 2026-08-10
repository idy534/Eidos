import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const packageJsonPath = path.join(root, "package.json");
const builderConfigPath = path.join(root, "electron-builder.yml");
const packageScriptPath = path.join(root, "scripts", "package-macos.sh");
const runtimeBuilderPath = path.join(root, "scripts", "build-macos-runtime.sh");


async function readPackagingFiles() {
  const [packageJson, builderConfig, packageScript, runtimeBuilder] = await Promise.all([
    readFile(packageJsonPath, "utf8"),
    readFile(builderConfigPath, "utf8"),
    readFile(packageScriptPath, "utf8"),
    readFile(runtimeBuilderPath, "utf8"),
  ]);
  return {
    packageJson: JSON.parse(packageJson),
    builderConfig,
    packageScript,
    runtimeBuilder,
  };
}


test("packaging commands and pinned electron-builder are declared", async () => {
  const { packageJson } = await readPackagingFiles();
  assert.equal(packageJson.devDependencies["electron-builder"], "26.15.3");
  assert.equal(packageJson.scripts["package:mac"], "bash scripts/package-macos.sh local");
  assert.equal(
    packageJson.scripts["package:mac:release"],
    "bash scripts/package-macos.sh release",
  );
  assert.equal(
    packageJson.scripts["test:electron-packaged"],
    "node scripts/packaged-electron-smoke.mjs",
  );
});


test("electron-builder keeps Runtime outside ASAR and targets only arm64 DMG", async () => {
  const { builderConfig } = await readPackagingFiles();
  assert.match(builderConfig, /^appId:\s+com\.idy\.eidos\s*$/m);
  assert.match(builderConfig, /^productName:\s+Eidos\s*$/m);
  assert.match(builderConfig, /^asar:\s+true\s*$/m);
  assert.match(builderConfig, /^\s*- from: build\/macos-runtime\s*$/m);
  assert.match(builderConfig, /^\s*to: runtime\s*$/m);
  assert.match(builderConfig, /target:\s+dmg/);
  assert.match(builderConfig, /^\s*arch:\s*\n\s*- arm64\s*$/m);
  assert.match(builderConfig, /dist\/main\/\*\*/);
  assert.match(builderConfig, /dist\/shared\/\*\*/);
  assert.match(builderConfig, /dist\/renderer\/\*\*/);
  assert.match(builderConfig, /!dist\/renderer-test\/\*\*/);
  assert.doesNotMatch(builderConfig, /runtime\/eidos_runtime/);
});


test("packaging scripts are executable shell entrypoints with a pinned Runtime Python", async () => {
  const { packageScript, runtimeBuilder } = await readPackagingFiles();
  await access(packageScriptPath);
  assert.match(packageScript, /^set -euo pipefail$/m);
  assert.match(packageScript, /Darwin/);
  assert.match(packageScript, /arm64/);
  assert.match(packageScript, /invalid mode|unsupported mode/i);
  assert.match(packageScript, /CSC_LINK|Developer ID/);
  assert.match(runtimeBuilder, /DEFAULT_PYTHON_VERSION="3\.12\.13"/);
});
