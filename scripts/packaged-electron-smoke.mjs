import assert from "node:assert/strict";
import { access, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const defaultReleaseRoot = path.join(root, "release");


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


async function findAppPath() {
  const entries = await readdir(defaultReleaseRoot, { withFileTypes: true });
  for (const entry of entries) {
    const candidate = path.join(defaultReleaseRoot, entry.name, "Eidos.app");
    try {
      if ((await stat(candidate)).isDirectory()) return candidate;
    } catch {
      // Continue searching the next builder output layout.
    }
  }
  const direct = path.join(defaultReleaseRoot, "Eidos.app");
  if ((await stat(direct)).isDirectory()) return direct;
  throw new Error(`Eidos.app was not found under ${defaultReleaseRoot}`);
}


function bundledEnvironment(dataDirectory, sentinelRoot) {
  const environment = {
    ...process.env,
    EIDOS_DATA_DIR: dataDirectory,
    EIDOS_ELECTRON_SMOKE: "1",
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
    PYTHONPATH: sentinelRoot,
    PYTHONHOME: path.join(sentinelRoot, "invalid-python-home"),
    EIDOS_PYTHON: path.join(sentinelRoot, "invalid-python"),
    PYTHONDONTWRITEBYTECODE: "0",
    PYTHONNOUSERSITE: "0",
  };
  // The packaged RuntimeClient is required to replace/remove these values.
  return environment;
}


async function verifyApplicationLayout(appPath) {
  const contentsRoot = path.join(appPath, "Contents");
  const resourcesRoot = path.join(contentsRoot, "Resources");
  const runtimeRoot = path.join(resourcesRoot, "runtime");
  const pythonRoot = path.join(runtimeRoot, "python");
  const appRoot = path.join(runtimeRoot, "app");
  const pythonExecutable = path.join(pythonRoot, "bin", "python3");
  const asarPath = path.join(resourcesRoot, "app.asar");
  const nodePtyRoot = path.join(
    resourcesRoot,
    "app.asar.unpacked",
    "node_modules",
    "node-pty",
    "prebuilds",
    "darwin-arm64",
  );

  assert.equal((await stat(path.join(contentsRoot, "MacOS", "Eidos"))).isFile(), true);
  assert.equal((await stat(asarPath)).isFile(), true, "Electron application must be in app.asar");
  assert.equal((await stat(path.join(nodePtyRoot, "pty.node"))).isFile(), true);
  assert.ok(
    (await stat(path.join(nodePtyRoot, "spawn-helper"))).mode & 0o111,
    "node-pty spawn helper must be executable",
  );
  assert.equal((await stat(pythonExecutable)).isFile(), true);
  assert.ok((await stat(pythonExecutable)).mode & 0o111, "bundled Python must be executable");
  assert.equal((await stat(path.join(appRoot, "eidos_runtime", "__main__.py"))).isFile(), true);
  assert.equal((await stat(path.join(appRoot, "eidos_runtime", "sandbox", "seatbelt.sbpl"))).isFile(), true);
  assert.equal((await stat(path.join(appRoot, "eidos_runtime", "sandbox", "file_commit_helper.py"))).isFile(), true);
  assert.equal((await stat(path.join(appRoot, "eidos_runtime", "sandbox", "sensitive_rules.json"))).isFile(), true);
  assert.equal((await stat(path.join(appRoot, "eidos_runtime", "extensions", "mcp_launcher.py"))).isFile(), true);
  const ripgrep = path.join(appRoot, "eidos_runtime", "resources", "bin", "ripgrep", "darwin-arm64", "rg");
  assert.equal((await stat(ripgrep)).isFile(), true);
  assert.ok((await stat(ripgrep)).mode & 0o111, "bundled Ripgrep must be executable");
  assert.equal((await stat(path.join(appRoot, "eidos_runtime", "resources", "bin", "ripgrep", "manifest.json"))).isFile(), true);

  const asarBytes = await readFile(asarPath);
  for (const runtimeSourceMarker of [
    "eidos_runtime/__main__.py",
    "eidos_runtime/sandbox/seatbelt.sbpl",
    "eidos_runtime/resources/bin/ripgrep/darwin-arm64/rg",
  ]) {
    assert.equal(
      asarBytes.includes(Buffer.from(runtimeSourceMarker)),
      false,
      `Runtime source must remain outside app.asar: ${runtimeSourceMarker}`,
    );
  }
  return { appRoot, appContentsRoot: contentsRoot, pythonExecutable, resourcesRoot, ripgrep };
}


async function verifyBundledPython({ appRoot, pythonExecutable, ripgrep }) {
  const check = String.raw`
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
from eidos_runtime.sandbox import seatbelt
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
assert os.environ.get("PYTHONHOME") is None
assert Path(os.environ["PYTHONPATH"]).resolve() == app_root
for module in ("anyio", "httpx", "mcp", "openai", "pydantic", "pydantic_ai", "pydantic_core", "tree_sitter"):
    __import__(module)

manifest_path = app_root / "eidos_runtime/resources/bin/ripgrep/manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
artifact = manifest["artifacts"]["darwin-arm64"]
binary = RipgrepBinaryResolver().resolve()
assert binary == Path(sys.argv[3]).resolve()
assert stat.S_IMODE(binary.stat().st_mode) & 0o111
assert hashlib.sha256(binary.read_bytes()).hexdigest() == artifact["sha256"]
version = subprocess.run([str(binary), "--version"], check=True, capture_output=True, text=True)
assert "ripgrep" in version.stdout

with tempfile.TemporaryDirectory(prefix="eidos-packaged-search-") as directory:
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

assert seatbelt.runtime_python_executable() == Path(sys.executable).resolve()
assert str(seatbelt.runtime_python_executable()) != "/Library/Developer/CommandLineTools/usr/bin/python3"
policy = (app_root / "eidos_runtime/sandbox/seatbelt.sbpl").read_text(encoding="utf-8")
assert "/Library/Developer/CommandLineTools/usr/bin/python3" not in policy
with tempfile.TemporaryDirectory(prefix="eidos-packaged-seatbelt-") as directory:
    workspace = Path(directory)
    source = workspace / "candidate"
    target = workspace / "committed"
    source.write_text("packaged", encoding="utf-8")
    assert seatbelt.secure_workspace_move(workspace, source, target, None) == "committed"
    assert target.read_text(encoding="utf-8") == "packaged"
print(json.dumps({"python": sys.executable, "runtime": eidos_runtime.__file__, "ripgrep": str(binary)}))
`;
  const result = await spawnCapture(
    pythonExecutable,
    ["-c", check, path.dirname(path.dirname(pythonExecutable)), appRoot, ripgrep],
    {
      cwd: appRoot,
      env: (() => {
        const environment = {
          ...process.env,
          PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
          PYTHONPATH: appRoot,
          PYTHONDONTWRITEBYTECODE: "1",
          PYTHONNOUSERSITE: "1",
        };
        delete environment.EIDOS_PYTHON;
        delete environment.PYTHONHOME;
        return environment;
      })(),
    },
  );
  assert.equal(result.code, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /"python":/);
  return result.stdout;
}


async function verifyRuntimeProtocol({ appRoot, pythonExecutable }) {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), "eidos-packaged-runtime-data-"));
  const environment = {
    ...process.env,
    EIDOS_DATA_DIR: dataDirectory,
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
    PYTHONPATH: appRoot,
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONNOUSERSITE: "1",
  };
  delete environment.EIDOS_PYTHON;
  delete environment.PYTHONHOME;
  const child = spawn(
    pythonExecutable,
    ["-u", "-m", "eidos_runtime"],
    { cwd: appRoot, env: environment, stdio: ["pipe", "pipe", "pipe"] },
  );
  const lines = createInterface({ input: child.stdout });
  const lineIterator = lines[Symbol.asyncIterator]();
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr = `${stderr}${chunk.toString("utf8")}`.slice(-1024 * 1024);
  });
  const exitPromise = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  let requestId = 0;
  const readNextLine = (method) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`packaged Runtime did not answer ${method} within 10 seconds\n${stderr}`));
    }, 10_000);
    lineIterator.next().then(
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
  const request = async (method, params) => {
    const id = `client-packaged-${requestId += 1}`;
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id, method, params })}\n`);
    while (true) {
      const next = await readNextLine(method);
      if (next.done) break;
      const message = JSON.parse(next.value);
      if (message.id === id) return message;
      if (message.id === null && message.error) {
        throw new Error(`packaged Runtime rejected ${method}: ${JSON.stringify(message.error)}`);
      }
    }
    throw new Error(`packaged Runtime closed before ${method} response\n${stderr}`);
  };

  try {
    const initialized = await request("initialize", {
      client: { name: "packaged-electron-smoke", version: "1" },
      protocolVersion: 1,
    });
    assert.equal(initialized.result.protocolVersion, 1);
    assert.equal(initialized.result.runtimeVersion, "0.3.0");
    const health = await request("runtime/health", {});
    assert.deepEqual(health.result, { state: "ready" });
    const shutdown = await request("runtime/shutdown", {});
    assert.ok(Object.hasOwn(shutdown, "result"));
    const exit = await Promise.race([
      exitPromise,
      new Promise((_, reject) => setTimeout(() => reject(new Error(`packaged Runtime did not exit\n${stderr}`)), 10_000)),
    ]);
    assert.equal(exit.code, 0, stderr);
    assert.ok((await readdir(dataDirectory)).length > 0, "Runtime did not create SQLite data");
  } finally {
    lines.close();
    if (!child.killed) child.kill("SIGTERM");
    await Promise.race([exitPromise, new Promise((resolve) => setTimeout(resolve, 2_000))]);
    await rm(dataDirectory, { recursive: true, force: true });
  }
}


async function verifyPackagedElectron({ appPath, appRoot }) {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "eidos-packaged-electron-"));
  const dataDirectory = path.join(temporaryRoot, "eidos-data");
  const userDataDirectory = path.join(temporaryRoot, "electron-user-data");
  const sentinelRoot = path.join(temporaryRoot, "python-sentinel");
  const markerPath = path.join(temporaryRoot, "python-sentinel-used");
  const executable = path.join(appPath, "Contents", "MacOS", "Eidos");
  await mkdir(sentinelRoot, { recursive: true });
  await writeFile(
    path.join(sentinelRoot, "sitecustomize.py"),
    `from pathlib import Path\nPath(${JSON.stringify(markerPath)}).write_text("used", encoding="utf-8")\n`,
    "utf8",
  );
  const environment = bundledEnvironment(dataDirectory, sentinelRoot);
  let output = "";
  try {
    const result = await new Promise((resolve, reject) => {
      const child = spawn(
        executable,
        [`--user-data-dir=${userDataDirectory}`],
        { cwd: appPath, env: environment, stdio: ["ignore", "pipe", "pipe"] },
      );
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        reject(new Error(`packaged Electron smoke timed out\n${output}`));
      }, 30_000);
      const capture = (chunk) => { output = `${output}${chunk.toString("utf8")}`.slice(-1024 * 1024); };
      child.stdout.on("data", capture);
      child.stderr.on("data", capture);
      child.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once("close", (code, signal) => {
        clearTimeout(timer);
        resolve({ code, signal });
      });
    });
    assert.equal(result.code, 0, output);
    assert.match(output, /Runtime initialized/);
    assert.match(output, /Runtime shutdown complete/);
    const marker = await access(markerPath).then(() => true, () => false);
    assert.equal(marker, false, "packaged Runtime inherited the host PYTHONPATH");
    console.log("Packaged Electron initialized the bundled Runtime and exited cleanly.");
  } finally {
    await rm(temporaryRoot, { recursive: true, force: true });
  }
}


async function main() {
  assert.equal(process.platform, "darwin", "packaged Electron supports macOS only");
  assert.equal(process.arch, "arm64", "packaged Electron supports macOS arm64 only");
  const appPath = path.resolve(process.argv[2] ?? await findAppPath());
  const layout = await verifyApplicationLayout(appPath);
  await verifyBundledPython(layout);
  await verifyRuntimeProtocol(layout);
  await verifyPackagedElectron({ ...layout, appPath });
  console.log(`Packaged layout, direct Runtime, Ripgrep, Seatbelt, SQLite, and Electron smoke passed: ${appPath}`);
}


try {
  await main();
} catch (error) {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
}
