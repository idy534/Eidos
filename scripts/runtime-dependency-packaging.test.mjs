import assert from "node:assert/strict";
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dependencyRoot = path.join(root, "resources", "runtime-dependencies");
const nodeDependencyRoot = path.join(dependencyRoot, "node");
const pythonDependencyRoot = path.join(dependencyRoot, "python");
const nodeReleasePath = path.join(nodeDependencyRoot, "node-release.json");
const nodePackagePath = path.join(nodeDependencyRoot, "package.json");
const nodeLockPath = path.join(nodeDependencyRoot, "pnpm-lock.yaml");
const pythonLockPath = path.join(pythonDependencyRoot, "requirements.lock");
const manifestScript = path.join(root, "scripts", "generate-runtime-manifest.mjs");
const runtimeBuilder = path.join(root, "scripts", "build-macos-runtime.sh");
const nodeBuilder = path.join(root, "scripts", "build-runtime-node.sh");
const pythonBuilder = path.join(root, "scripts", "build-runtime-python.mjs");
const pythonAudit = path.join(root, "scripts", "audit-python.sh");


function run(executable, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      cwd: root,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      ...options,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
}


test("runtime dependency source pins the approved Node LTS and docx release", async () => {
  const release = JSON.parse(await readFile(nodeReleasePath, "utf8"));
  const packageJson = JSON.parse(await readFile(nodePackagePath, "utf8"));
  const lock = await readFile(nodeLockPath, "utf8");

  assert.deepEqual(release, {
    version: "24.20.0",
    target: "darwin-arm64",
    archive: "node-v24.20.0-darwin-arm64.tar.xz",
    url: "https://nodejs.org/download/release/v24.20.0/node-v24.20.0-darwin-arm64.tar.xz",
    checksumsUrl: "https://nodejs.org/download/release/v24.20.0/SHASUMS256.txt",
    sha256: "b7bf7707070b950ba1ec5f1af3bb6de0f2b1962c5033973d94068ab021ef3014",
  });
  assert.equal(packageJson.private, true);
  assert.equal(packageJson.type, "module");
  assert.deepEqual(packageJson.dependencies, { docx: "9.7.1" });
  assert.match(lock, /docx@9\.7\.1/);
  assert.match(lock, /resolution:/);
});


test("Python skill lock is an audited, bounded closure", async () => {
  const lock = await readFile(pythonLockPath, "utf8");
  assert.match(lock, /^python-docx==1\.2\.0\b.*--hash=sha256:[0-9a-f]{64}/m);
  assert.match(lock, /^lxml==6\.1\.2\b.*--hash=sha256:[0-9a-f]{64}/m);
  assert.match(lock, /^typing-extensions==4\.16\.0\b.*--hash=sha256:[0-9a-f]{64}/m);
  assert.match(lock, /import-name=docx/);
  assert.match(lock, /import-name=lxml/);
  assert.match(lock, /import-name=typing_extensions/);
  assert.doesNotMatch(lock, /pydantic|anyio|openai|pillow/);
});


test("the root Runtime does not declare the isolated Skill dependency", async () => {
  const pyproject = await readFile(path.join(root, "pyproject.toml"), "utf8");
  const packageJson = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
  assert.doesNotMatch(pyproject, /^\s*"python-docx/m);
  assert.equal(packageJson.dependencies?.docx, undefined);
  assert.equal(packageJson.devDependencies?.docx, undefined);
  assert.equal(packageJson.scripts["audit:runtime-node"], "pnpm --dir resources/runtime-dependencies/node audit --prod --audit-level high");
  assert.match(packageJson.scripts["audit:python"], /audit-python\.sh/);
  assert.match(packageJson.scripts["audit:python"], /audit:runtime-node/);
  const auditScript = await readFile(pythonAudit, "utf8");
  assert.match(auditScript, /resources\/runtime-dependencies\/python[\s\S]*requirements\.lock/);
  assert.match(auditScript, /pip-audit[\s\S]*runtime_requirements_file/);
});


test("Runtime builder wires isolated dependencies and the E-owned loader", async () => {
  const [builder, nodeBuilderSource, pythonBuilderSource] = await Promise.all([
    readFile(runtimeBuilder, "utf8"),
    readFile(nodeBuilder, "utf8"),
    readFile(pythonBuilder, "utf8"),
  ]);
  assert.match(builder, /build-runtime-node\.sh/);
  assert.match(builder, /build-runtime-python\.mjs/);
  assert.match(builder, /generate-runtime-manifest\.mjs/);
  assert.match(builder, /dependencies\/node\/runtime-loader\.mjs/);
  assert.match(builder, /env -u NODE_OPTIONS/);
  assert.match(nodeBuilderSource, /SHASUMS256\.txt/);
  assert.match(nodeBuilderSource, /release\.sha256|EXPECTED_SHA256/);
  assert.match(nodeBuilderSource, /LICENSE/);
  assert.match(nodeBuilderSource, /rsync -aL/);
  assert.match(nodeBuilderSource, /RUNTIME_NODE_MODULES|runtime-loader\.mjs/);
  assert.match(pythonBuilderSource, /requirements\.lock/);
  assert.match(pythonBuilderSource, /top_level\.txt/);
  assert.match(pythonBuilderSource, /uv/);
  assert.match(pythonBuilderSource, /--require-hashes/);
  assert.match(pythonBuilderSource, /--no-deps/);
  assert.match(pythonBuilderSource, /atomic|rename/i);
  assert.doesNotMatch(pythonBuilderSource, /sourceApp|source-app/);
});


test("bundled smoke keeps isolated Python packages ahead of the App runtime", async () => {
  const smoke = await readFile(
    path.join(root, "scripts/bundled-runtime-smoke.mjs"),
    "utf8",
  );
  assert.match(smoke, /PYTHONPATH: appRoot/);
  assert.match(
    smoke,
    /PYTHONPATH: \[pythonDependencyRoot, appRoot\]\.join\(path\.delimiter\)/,
  );
  assert.doesNotMatch(
    smoke,
    /PYTHONPATH: \[appRoot, pythonDependencyRoot\]\.join\(path\.delimiter\)/,
  );
  assert.match(
    smoke,
    /cwd: os\.tmpdir\(\), env: bundledDependencyEnvironment\(os\.tmpdir\(\)\)/,
  );
});


test("Python builder installs the audited lock into an atomic isolated target", async () => {
  const buildRoot = path.join(root, "build", "macos-runtime");
  await mkdir(buildRoot, { recursive: true });
  const fixture = await mkdtemp(path.join(buildRoot, "python-builder-"));
  const target = path.join(fixture, "dependencies", "python");
  const fakeBin = path.join(fixture, "bin");
  const fakeUv = path.join(fakeBin, "uv");
  const lock = path.join(fixture, "requirements.lock");
  const topLevel = path.join(fixture, "top_level.txt");
  const uvLog = path.join(fixture, "uv-args.json");
  try {
    await mkdir(fakeBin, { recursive: true });
    await writeFile(
      lock,
      [
        "python-docx==1.2.0 --hash=sha256:" + "a".repeat(64) + " # import-name=docx",
        "lxml==6.1.2 --hash=sha256:" + "b".repeat(64) + " # import-name=lxml",
        "typing-extensions==4.16.0 --hash=sha256:" + "c".repeat(64) + " # import-name=typing_extensions",
        "",
      ].join("\n"),
      "utf8",
    );
    await writeFile(
      topLevel,
      [
        "python-docx==1.2.0: docx",
        "lxml==6.1.2: lxml",
        "typing-extensions==4.16.0: typing_extensions",
        "",
      ].join("\n"),
      "utf8",
    );
    await writeFile(
      fakeUv,
      `#!/usr/bin/env node
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const argumentsList = process.argv.slice(2);
const targetIndex = argumentsList.indexOf("--target");
if (targetIndex < 0) throw new Error("missing target");
const target = argumentsList[targetIndex + 1];
writeFileSync(process.env.EIDOS_TEST_UV_LOG, JSON.stringify(argumentsList));
writeFileSync(path.join(target, ".lock"), "build-only\\n");
const packages = [
  ["docx", "python_docx-1.2.0.dist-info", "python-docx", "1.2.0", "package"],
  ["lxml", "lxml-6.1.2.dist-info", "lxml", "6.1.2", "package"],
  ["typing_extensions.py", "typing_extensions-4.16.0.dist-info", "typing-extensions", "4.16.0", "module"],
];
for (const [importRoot, metadataRoot, name, version, kind] of packages) {
  const importPath = path.join(target, importRoot);
  mkdirSync(kind === "package" ? importPath : path.dirname(importPath), { recursive: true });
  writeFileSync(kind === "package" ? path.join(importPath, "__init__.py") : importPath, "# isolated\\n");
  const metadataPath = path.join(target, metadataRoot);
  mkdirSync(metadataPath, { recursive: true });
  writeFileSync(path.join(metadataPath, "METADATA"), "Metadata-Version: 2.1\\nName: " + name + "\\nVersion: " + version + "\\n");
}
`,
      "utf8",
    );
    await chmod(fakeUv, 0o755);
    await mkdir(target, { recursive: true });
    await writeFile(path.join(target, "stale.txt"), "must be replaced\n", "utf8");
    const result = await run(process.execPath, [
      pythonBuilder,
      "--target", target,
      "--requirements", lock,
      "--top-level", topLevel,
      "--python", process.execPath,
    ], {
      env: {
        ...process.env,
        EIDOS_TEST_UV_LOG: uvLog,
        PATH: `${fakeBin}${path.delimiter}${process.env.PATH ?? ""}`,
      },
    });
    assert.equal(result.code, 0, `${result.stdout}\n${result.stderr}`);
    const args = JSON.parse(await readFile(uvLog, "utf8"));
    assert.ok(args.includes("--target"));
    assert.ok(args.includes("--require-hashes"));
    assert.ok(args.includes("--no-deps"));
    assert.ok(args.includes("--link-mode"));
    assert.ok(args.includes("copy"));
    assert.equal(await readFile(path.join(target, "docx", "__init__.py"), "utf8"), "# isolated\n");
    await assert.rejects(() => readFile(path.join(target, "stale.txt"), "utf8"));
    await assert.rejects(() => readFile(path.join(target, ".lock"), "utf8"));
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});


async function createManifestFixture() {
  const fixture = await mkdtemp(path.join(os.tmpdir(), "eidos-runtime-manifest-"));
  const files = new Map([
    ["python/bin/python3", "#!/bin/sh\nexit 0\n"],
    ["python/lib/python3.12/os.py", "import posix\n"],
    ["python/lib/python3.12/libexec/stdlib-tool", "#!/bin/sh\nexit 0\n"],
    ["app/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg", "#!/bin/sh\nexit 0\n"],
    ["app/eidos_runtime/bin/runtime-helper", "#!/bin/sh\nexit 0\n"],
    ["dependencies/node/bin/node", "node-binary"],
    ["dependencies/node/runtime-loader.mjs", "export {};\n"],
    ["dependencies/node/node_modules/docx/index.cjs", "module.exports = {};\n"],
    ["dependencies/node/node_modules/docx/package.json", "{\"name\":\"docx\",\"version\":\"9.7.1\"}\n"],
    ["dependencies/python/docx/__init__.py", "__version__ = '1.2.0'\n"],
    ["dependencies/python/python_docx-1.2.0.dist-info/METADATA", "Name: python-docx\nVersion: 1.2.0\n"],
  ]);
  for (const [relative, content] of files) {
    const target = path.join(fixture, relative);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, content, { encoding: "utf8", mode: 0o644 });
  }
  await chmod(path.join(fixture, "python/bin/python3"), 0o755);
  await chmod(
    path.join(fixture, "app/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg"),
    0o755,
  );
  await chmod(path.join(fixture, "python/lib/python3.12/libexec/stdlib-tool"), 0o755);
  await chmod(path.join(fixture, "app/eidos_runtime/bin/runtime-helper"), 0o755);
  await chmod(path.join(fixture, "dependencies/node/bin/node"), 0o755);
  await symlink(
    "runtime-helper",
    path.join(fixture, "app/eidos_runtime/bin/runtime-helper-alias"),
  );
  return fixture;
}


test("manifest generator emits relocation-safe inventory and detects tampering", async () => {
  const fixture = await createManifestFixture();
  const lock = path.join(fixture, "python.lock");
  await writeFile(
    lock,
    "python-docx==1.2.0 --hash=sha256:" + "a".repeat(64) + " # import-name=docx\n",
    "utf8",
  );
  const generated = await run(process.execPath, [
    manifestScript,
    "--bundle-root", fixture,
    "--bundle-id", "eidos-runtime",
    "--bundle-version", "0.3.0",
    "--python-version", "3.12.13",
    "--node-version", "24.20.0",
    "--ripgrep-version", "14.1.1",
    "--python-lock", lock,
    "--python-path", "dependencies/python",
    "--node-loader", "dependencies/node/runtime-loader.mjs",
  ]);
  assert.equal(generated.code, 0, `${generated.stdout}\n${generated.stderr}`);

  const manifest = JSON.parse(
    await readFile(path.join(fixture, "runtime.json"), "utf8"),
  );
  assert.equal(manifest.schemaVersion, 1);
  assert.equal(manifest.target, "darwin-arm64");
  assert.equal(manifest.nodeLoader, "dependencies/node/runtime-loader.mjs");
  assert.equal(manifest.nodeModules, "dependencies/node/node_modules");
  assert.deepEqual(manifest.pythonPath, ["dependencies/python"]);
  assert.ok(manifest.files.length >= 6);
  assert.ok(manifest.files.every(({ path: relative }) => !relative.startsWith("/")));
  assert.ok(manifest.files.every(({ path: relative }) => !relative.split("/").includes("..")));
  assert.ok(!manifest.files.some(({ path: relative }) => relative === "runtime.json"));
  for (const executable of manifest.executables) {
    assert.ok(manifest.files.some(({ path: relative }) => relative === executable.path));
  }
  assert.ok(manifest.executables.some(({ path: relative }) => relative === "python/lib/python3.12/libexec/stdlib-tool"));
  assert.ok(manifest.executables.some(({ path: relative }) => relative.startsWith("app/eidos_runtime/bin/runtime-helper")));
  assert.equal(new Set(manifest.files.map(({ path: relative }) => relative)).size, manifest.files.length);

  const firstManifestBytes = await readFile(path.join(fixture, "runtime.json"));
  const regenerated = await run(process.execPath, [
    manifestScript,
    "--bundle-root", fixture,
    "--bundle-id", "eidos-runtime",
    "--bundle-version", "0.3.0",
    "--python-version", "3.12.13",
    "--node-version", "24.20.0",
    "--ripgrep-version", "14.1.1",
    "--python-lock", lock,
    "--python-path", "dependencies/python",
    "--node-loader", "dependencies/node/runtime-loader.mjs",
  ]);
  assert.equal(regenerated.code, 0, `${regenerated.stdout}\n${regenerated.stderr}`);
  assert.deepEqual(await readFile(path.join(fixture, "runtime.json")), firstManifestBytes);

  const verified = await run(process.execPath, [
    manifestScript,
    "--verify",
    "--bundle-root", fixture,
  ]);
  assert.equal(verified.code, 0, `${verified.stdout}\n${verified.stderr}`);

  await writeFile(
    path.join(fixture, "dependencies/node/node_modules/docx/index.cjs"),
    "tampered\n",
    "utf8",
  );
  const tampered = await run(process.execPath, [
    manifestScript,
    "--verify",
    "--bundle-root", fixture,
  ]);
  assert.notEqual(tampered.code, 0);
  assert.match(`${tampered.stdout}\n${tampered.stderr}`, /hash|tamper/i);

  const invalidPath = await run(process.execPath, [
    manifestScript,
    "--bundle-root", fixture,
    "--bundle-id", "eidos-runtime",
    "--bundle-version", "0.3.0",
    "--python-version", "3.12.13",
    "--node-version", "24.20.0",
    "--ripgrep-version", "14.1.1",
    "--python-lock", lock,
    "--python-path", "dependencies/./python",
  ]);
  assert.notEqual(invalidPath.code, 0);
  assert.match(`${invalidPath.stdout}\n${invalidPath.stderr}`, /relative|dot|path/i);
  await rm(fixture, { recursive: true, force: true });
});
