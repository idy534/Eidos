import assert from "node:assert/strict";
import { mkdtemp, readdir, rm, stat } from "node:fs/promises";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleRoot = path.join(root, "build", "macos-runtime");
const pythonRoot = path.join(bundleRoot, "python");
const appRoot = path.join(bundleRoot, "app");
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
from eidos_runtime.workspace.discovery_scope import WorkspaceDiscoveryScope
from eidos_runtime.workspace.search_driver import (
    RipgrepBinaryResolver,
    RipgrepSearchDriver,
    WorkspaceSearchRequest,
)

python_root = Path(sys.argv[1]).resolve()
app_root = Path(sys.argv[2]).resolve()
assert Path(sys.executable).resolve().is_relative_to(python_root)
assert Path(eidos_runtime.__file__).resolve().is_relative_to(app_root)
for module in ("anyio", "httpx", "mcp", "openai", "pydantic", "pydantic_ai", "pydantic_core", "tree_sitter"):
    __import__(module)

required = (
    "eidos_runtime/__main__.py",
    "eidos_runtime/sandbox/seatbelt.sbpl",
    "eidos_runtime/sandbox/file_commit_helper.py",
    "eidos_runtime/sandbox/sensitive_rules.json",
    "eidos_runtime/extensions/mcp_launcher.py",
    "eidos_runtime/resources/bin/ripgrep/manifest.json",
    "eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg",
)
for relative in required:
    assert (app_root / relative).is_file(), relative

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
    ["-c", pythonCheck, pythonRoot, appRoot],
    { cwd: appRoot, env: bundledEnvironment(os.tmpdir()) },
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
