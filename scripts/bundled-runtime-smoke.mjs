import assert from "node:assert/strict";
import { mkdtemp, mkdir, readdir, rm, stat, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleRoot = path.join(root, "build", "macos-runtime");
const pythonRoot = path.join(bundleRoot, "python");
const appRoot = path.join(bundleRoot, "app");
const nodeRoot = path.join(bundleRoot, "dependencies", "node");
const nodeModulesRoot = path.join(nodeRoot, "node_modules");
const nodeExecutable = path.join(nodeRoot, "bin", "node");
const nodeLoader = path.join(nodeRoot, "runtime-loader.mjs");
const pythonDependencyRoot = path.join(bundleRoot, "dependencies", "python");
const pythonExecutable = path.join(pythonRoot, "bin", "python3");


function bundledEnvironment(dataDirectory) {
  const environment = {
    ...process.env,
    EIDOS_DATA_DIR: dataDirectory,
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONNOUSERSITE: "1",
    PYTHONPATH: appRoot,
  };
  delete environment.EIDOS_PYTHON;
  delete environment.PYTHONHOME;
  delete environment.NODE_OPTIONS;
  return environment;
}


function bundledDependencyEnvironment(dataDirectory) {
  return {
    ...bundledEnvironment(dataDirectory),
    // Match dependency-bound Shell semantics: the isolated package root wins,
    // while the App root is present only so this smoke can inspect the catalog.
    PYTHONPATH: [pythonDependencyRoot, appRoot].join(path.delimiter),
  };
}


function bundledNodeEnvironment() {
  const environment = {
    ...process.env,
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
    RUNTIME_NODE_MODULES: nodeModulesRoot,
  };
  delete environment.NODE_OPTIONS;
  environment.NODE_OPTIONS = `--import=${pathToFileURL(nodeLoader).href}`;
  return environment;
}


function spawnCapture(executable, args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      ...options,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const append = (value, chunk) => `${value}${chunk.toString("utf8")}`.slice(-1024 * 1024);
    child.stdout.on("data", (chunk) => { stdout = append(stdout, chunk); });
    child.stderr.on("data", (chunk) => { stderr = append(stderr, chunk); });
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
}


function waitForExit(child, exitPromise, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`bundled Runtime did not exit within ${timeoutMs}ms`)),
      timeoutMs,
    );
    exitPromise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}


async function verifyBundledNodeDependencies() {
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-bundled-node-"));
  const conflictingPackageRoot = path.join(fixtureRoot, "node_modules", "docx");
  const cjsScript = path.join(fixtureRoot, "require-docx.cjs");
  const esmScript = path.join(fixtureRoot, "import-docx.mjs");
  try {
    await mkdir(conflictingPackageRoot, { recursive: true });
    await writeFile(
      path.join(conflictingPackageRoot, "package.json"),
      JSON.stringify({
        name: "docx",
        version: "0.0.0-workspace-conflict",
        exports: { require: "./index.cjs", import: "./index.mjs" },
      }),
      "utf8",
    );
    await writeFile(
      path.join(conflictingPackageRoot, "index.cjs"),
      "throw new Error('workspace docx was resolved');\n",
      "utf8",
    );
    await writeFile(
      path.join(conflictingPackageRoot, "index.mjs"),
      "throw new Error('workspace docx was resolved');\n",
      "utf8",
    );
    await writeFile(
      cjsScript,
      String.raw`const path = require("node:path");
const { Document, Packer } = require("docx");
const resolved = require.resolve("docx");
if (!resolved.startsWith(process.env.RUNTIME_NODE_MODULES)) throw new Error(resolved);
if (typeof Document !== "function" || typeof Packer?.toBuffer !== "function") throw new Error("docx CJS exports are incomplete");
process.stdout.write(JSON.stringify({ format: "cjs", resolved }));
`,
      "utf8",
    );
    await writeFile(
      esmScript,
      String.raw`import { createRequire } from "node:module";
import { Document, Packer } from "docx";
const require = createRequire(import.meta.url);
const resolved = require.resolve("docx");
if (!resolved.startsWith(process.env.RUNTIME_NODE_MODULES)) throw new Error(resolved);
if (typeof Document !== "function" || typeof Packer?.toBuffer !== "function") throw new Error("docx ESM exports are incomplete");
process.stdout.write(JSON.stringify({ format: "esm", resolved }));
`,
      "utf8",
    );

    const environment = bundledNodeEnvironment();
    assert.ok(path.isAbsolute(environment.RUNTIME_NODE_MODULES));
    assert.match(environment.NODE_OPTIONS, /--import=file:/);
    if (root.includes(" ")) assert.match(environment.NODE_OPTIONS, /%20/);
    for (const script of [cjsScript, esmScript]) {
      const result = await spawnCapture(
        nodeExecutable,
        [script],
        { cwd: fixtureRoot, env: environment },
      );
      assert.equal(result.code, 0, `${result.stdout}\n${result.stderr}`);
      const output = JSON.parse(result.stdout);
      assert.ok(output.resolved.startsWith(nodeModulesRoot));
    }
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
}


async function verifyBundledImportsAndRipgrep() {
  const pythonCheck = String.raw`
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time

import eidos_runtime
import lark
from eidos_runtime.infrastructure.runtime_dependencies import RuntimeDependencyCatalog
from eidos_runtime.tools.contracts import ApplyPatchInput
from eidos_runtime.workspace.discovery_scope import WorkspaceDiscoveryScope
from eidos_runtime.workspace.codex_patch import encode_patch, parse_patch
from eidos_runtime.workspace.search_driver import (
    RipgrepBinaryResolver,
    RipgrepSearchDriver,
    WorkspaceSearchRequest,
)

python_root = Path(sys.argv[1]).resolve()
app_root = Path(sys.argv[2]).resolve()
dependency_root = Path(sys.argv[3]).resolve()
bundle_root = Path(sys.argv[4]).resolve()
assert Path(sys.executable).resolve().is_relative_to(python_root)
assert Path(eidos_runtime.__file__).resolve().is_relative_to(app_root)
snapshot = RuntimeDependencyCatalog.from_manifest(bundle_root / "runtime.json").snapshot()
assert snapshot.python_path == (str(dependency_root),), snapshot.python_path
for module in ("anyio", "httpx", "lark", "mcp", "openai", "pydantic", "pydantic_ai", "pydantic_core", "tree_sitter"):
    __import__(module)
assert Path(lark.__file__).resolve().is_relative_to(app_root), lark.__file__
import docx
import lxml
import typing_extensions
assert Path(docx.__file__).resolve().is_relative_to(dependency_root), docx.__file__
assert Path(lxml.__file__).resolve().is_relative_to(dependency_root), lxml.__file__
assert Path(typing_extensions.__file__).resolve().is_relative_to(dependency_root), typing_extensions.__file__

required = (
    "eidos_runtime/__main__.py",
    "eidos_runtime/sandbox/seatbelt.sbpl",
    "eidos_runtime/sandbox/file_commit_helper.py",
    "eidos_runtime/sandbox/sensitive_rules.json",
    "eidos_runtime/extensions/mcp_launcher.py",
    "eidos_runtime/workspace/apply_patch.lark",
    "eidos_runtime/resources/bin/ripgrep/manifest.json",
    "eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg",
)
for relative in required:
    assert (app_root / relative).is_file(), relative

def encode_and_parse_add(path, content):
    request = ApplyPatchInput.model_validate({
        "changes": ({
            "type": "add",
            "path": path,
            "content": content,
        },),
    })
    canonical = encode_patch(request)
    parsed = parse_patch(canonical)
    assert len(parsed) == 1
    assert parsed[0].path == path
    assert parsed[0].content == content
    return canonical

canonical_patch = encode_and_parse_add(
    "bundled-apply-patch.txt",
    "one\n\ntwo\n",
)
assert canonical_patch == (
    "*** Begin Patch\n"
    "*** Add File: bundled-apply-patch.txt\n"
    "+one\n"
    "+\n"
    "+two\n"
    "*** End Patch"
)
empty_patch = encode_and_parse_add("bundled-empty.txt", "")
assert empty_patch == (
    "*** Begin Patch\n"
    "*** Add File: bundled-empty.txt\n"
    "*** End Patch"
)
single_newline_patch = encode_and_parse_add("bundled-single-newline.txt", "\n")
assert single_newline_patch == (
    "*** Begin Patch\n"
    "*** Add File: bundled-single-newline.txt\n"
    "+\n"
    "*** End Patch"
)
double_newline_patch = encode_and_parse_add("bundled-double-newline.txt", "\n\n")
assert double_newline_patch == (
    "*** Begin Patch\n"
    "*** Add File: bundled-double-newline.txt\n"
    "+\n"
    "+\n"
    "*** End Patch"
)

manifest_path = app_root / "eidos_runtime/resources/bin/ripgrep/manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
artifact = manifest["artifacts"]["darwin-arm64"]
binary = RipgrepBinaryResolver().resolve()
assert binary == app_root / "eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg"
assert stat.S_IMODE(binary.stat().st_mode) & 0o111
assert hashlib.sha256(binary.read_bytes()).hexdigest() == artifact["sha256"]
version = subprocess.run(
    [str(binary), "--version"], check=True, capture_output=True, text=True,
)
assert "ripgrep" in version.stdout

with tempfile.TemporaryDirectory(prefix="eidos-bundled-search-") as directory:
    workspace = Path(directory)
    (workspace / "hello.go").write_text("package main\nfunc hello() {}\n", encoding="utf-8")
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        scope = WorkspaceDiscoveryScope.load(descriptor)
    finally:
        os.close(descriptor)
    result = RipgrepSearchDriver().search(
        WorkspaceSearchRequest(
            query="hello",
            workspace_path=workspace,
            deadline=time.monotonic() + 5,
            max_results=100,
            max_preview_characters=300,
            discovery_scope=scope,
        ),
        threading.Event(),
    )
    assert [(match.path, match.line) for match in result.matches] == [("hello.go", 2)]
print(json.dumps({"python": sys.executable, "runtime": eidos_runtime.__file__, "ripgrep": str(binary)}))
`;
  const result = await spawnCapture(
    pythonExecutable,
    ["-c", pythonCheck, pythonRoot, appRoot, pythonDependencyRoot, bundleRoot],
    { cwd: os.tmpdir(), env: bundledDependencyEnvironment(os.tmpdir()) },
  );
  assert.equal(result.code, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /"python":/);
}


async function verifyRuntimeProtocol() {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-bundled-runtime-data-"));
  const environment = bundledEnvironment(dataDirectory);
  const child = spawn(
    pythonExecutable,
    ["-u", "-m", "eidos_runtime"],
    { cwd: appRoot, env: environment, stdio: ["pipe", "pipe", "pipe"] },
  );
  const lines = createInterface({ input: child.stdout });
  let stderr = "";
  let closed = false;
  const lineIterator = lines[Symbol.asyncIterator]();
  child.stderr.on("data", (chunk) => {
    stderr = `${stderr}${chunk.toString("utf8")}`.slice(-1024 * 1024);
  });
  const exitPromise = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => {
      closed = true;
      resolve({ code, signal });
    });
  });
  let requestId = 0;
  const request = async (method, params) => {
    const id = `client-bundled-${requestId += 1}`;
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    while (true) {
      const next = await lineIterator.next();
      if (next.done) break;
      const message = JSON.parse(next.value);
      if (message.id === id) return message;
    }
    throw new Error(`bundled Runtime closed before ${method} response\n${stderr}`);
  };

  try {
    const initialized = await request("initialize", {
      client: { name: "bundled-runtime-smoke", version: "1" },
      protocolVersion: 1,
    });
    assert.equal(initialized.result.protocolVersion, 1);
    assert.equal(initialized.result.runtimeVersion, "0.3.0");

    const health = await request("runtime/health", {});
    assert.deepEqual(health.result, { state: "ready" });

    const shutdown = await request("runtime/shutdown", {});
    assert.ok(Object.hasOwn(shutdown, "result"));
    const exit = await waitForExit(child, exitPromise, 10_000);
    assert.equal(exit.code, 0, stderr);
    assert.ok((await readdir(dataDirectory)).length > 0, "Runtime did not create SQLite data");
  } finally {
    lines.close();
    if (!closed) child.kill("SIGTERM");
    await Promise.race([exitPromise, new Promise((resolve) => setTimeout(resolve, 2_000))]);
    await rm(dataDirectory, { recursive: true, force: true });
  }
}


async function main() {
  assert.equal(process.platform, "darwin", "bundled Runtime supports macOS only");
  assert.equal(process.arch, "arm64", "bundled Runtime supports macOS arm64 only");
  assert.ok(!pythonExecutable.includes(".venv"));
  assert.ok(!pythonExecutable.startsWith("/usr/bin/"));
  const nodeMetadata = await stat(nodeExecutable);
  assert.ok((nodeMetadata.mode & 0o111) !== 0);
  assert.equal((await spawnCapture(nodeExecutable, ["--version"], {
    cwd: appRoot,
    env: bundledNodeEnvironment(),
  })).code, 0);
  await verifyBundledNodeDependencies();
  const pythonMetadata = await stat(pythonExecutable);
  assert.ok((pythonMetadata.mode & 0o111) !== 0);
  await verifyBundledImportsAndRipgrep();
  await verifyRuntimeProtocol();
  console.log("Bundled Runtime imports, Ripgrep, JSON-RPC, health, SQLite, and shutdown passed.");
}


try {
  await main();
} catch (error) {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
}
