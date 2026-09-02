import assert from "node:assert/strict";
import { chmod, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createMacSign } from "./macos-sign.mjs";


async function writeExecutable(filePath, content) {
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, content, "utf8");
  await chmod(filePath, 0o755);
}


test("mac sign wrapper refreshes manifest after nested children and preserves options", async () => {
  const app = await mkdtemp(path.join(os.tmpdir(), "eidos-mac-sign-"));
  const runtime = path.join(app, "Contents", "Resources", "runtime");
  const node = path.join(runtime, "dependencies", "node", "bin", "node");
  const loader = path.join(runtime, "dependencies", "node", "runtime-loader.mjs");
  const docx = path.join(runtime, "dependencies", "node", "node_modules", "docx", "index.cjs");
  const python = path.join(runtime, "dependencies", "python", "docx", "__init__.py");
  const pythonExecutable = path.join(runtime, "python", "bin", "python3");
  const pythonStdlib = path.join(runtime, "python", "lib", "python3.12", "os.py");
  const rg = path.join(runtime, "app", "eidos_runtime", "resources", "bin", "ripgrep", "darwin-arm64", "rg");
  try {
    await writeExecutable(node, "child-before-sign\n");
    await writeFile(loader, "export {}\n", "utf8");
    await mkdir(path.dirname(docx), { recursive: true });
    await writeFile(docx, "module.exports = {}\n", "utf8");
    await mkdir(path.dirname(python), { recursive: true });
    await writeFile(python, "__version__ = '1.2.0'\n", "utf8");
    await writeExecutable(pythonExecutable, "python\n");
    await mkdir(path.dirname(pythonStdlib), { recursive: true });
    await writeFile(pythonStdlib, "import posix\n", "utf8");
    await writeExecutable(rg, "rg\n");
    await writeFile(
      path.join(runtime, "runtime.json"),
      JSON.stringify({
        schemaVersion: 1,
        bundleId: "test.bundle",
        bundleVersion: "0.3.0",
        target: "darwin-arm64",
        executables: [
          { name: "node", path: "dependencies/node/bin/node", version: "24.20.0", sha256: "0".repeat(64) },
          { name: "rg", path: "app/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg", version: "14.1.1", sha256: "0".repeat(64) },
        ],
        pythonPath: ["dependencies/python"],
        pythonPackages: [{ name: "python-docx", importName: "docx", version: "1.2.0" }],
        nodeModules: "dependencies/node/node_modules",
        nodePackages: [{ name: "docx", version: "9.7.1" }],
        nodeLoader: "dependencies/node/runtime-loader.mjs",
        nativeBinPaths: [],
        files: [
          { path: "dependencies/node/bin/node", sha256: "0".repeat(64) },
          { path: "dependencies/node/runtime-loader.mjs", sha256: "0".repeat(64) },
          { path: "dependencies/node/node_modules/docx/index.cjs", sha256: "0".repeat(64) },
          { path: "dependencies/python/docx/__init__.py", sha256: "0".repeat(64) },
          { path: "app/eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg", sha256: "0".repeat(64) },
        ],
        preservedMetadata: { signingProfile: "distribution" },
      }, null, 2) + "\n",
      "utf8",
    );

    const callbackOrder = [];
    const originalOptions = {
      app,
      identity: { name: "Developer ID Application: Test" },
      platform: "darwin",
      binaries: ["extra-binary"],
      optionsForFile(filePath) {
        callbackOrder.push(path.basename(filePath));
        return { entitlements: `${filePath}.plist` };
      },
    };
    let receivedOptions;
    const fakeSignAsync = async (options) => {
      receivedOptions = options;
      const child = path.join(app, "Contents", "Resources", "nested-child");
      const childOptions = options.optionsForFile(child);
      assert.equal(childOptions.entitlements, `${child}.plist`);
      await writeFile(node, "child-after-sign\n", "utf8");
      const appOptions = options.optionsForFile(app);
      assert.equal(appOptions.entitlements, `${app}.plist`);
      return "signed";
    };

    const sign = createMacSign({ signAsync: fakeSignAsync });
    const result = await sign(originalOptions, { appInfo: { type: "app" } });
    assert.equal(result, "signed");
    assert.deepEqual(callbackOrder, ["nested-child", path.basename(app)]);
    assert.equal(receivedOptions.app, app);
    assert.deepEqual(receivedOptions.identity, originalOptions.identity);
    assert.deepEqual(receivedOptions.binaries, originalOptions.binaries);
    assert.notEqual(receivedOptions, originalOptions);

    const manifest = JSON.parse(
      await readFile(path.join(runtime, "runtime.json"), "utf8"),
    );
    assert.equal(manifest.preservedMetadata.signingProfile, "distribution");
    assert.equal(manifest.pythonPath.length, 1);
    assert.notEqual(manifest.executables[0].sha256, "");
    assert.equal(manifest.files.length, 7);
    assert.notEqual(manifest.files[0].sha256, "");
  } finally {
    await rm(app, { recursive: true, force: true });
  }
});


test("mac sign wrapper uses a synchronous refresh hook before original options", async () => {
  const calls = [];
  const app = "/tmp/Eidos Test.app";
  const originalOptions = {
    app,
    optionsForFile(filePath) {
      calls.push(["original", filePath]);
      return { filePath };
    },
  };
  const fakeSignAsync = async (options) => {
    const result = options.optionsForFile(app);
    assert.deepEqual(result, { filePath: app });
    return options;
  };
  const sign = createMacSign({
    signAsync: fakeSignAsync,
    refreshManifest({ bundleRoot }) {
      calls.push(["refresh", bundleRoot]);
    },
  });
  await sign(originalOptions, null);
  assert.deepEqual(calls, [
    ["refresh", path.join(app, "Contents", "Resources", "runtime")],
    ["original", app],
  ]);
});
