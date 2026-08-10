import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleRoot = path.join(root, "build", "macos-runtime");
const pythonRoot = path.join(bundleRoot, "python");
const appRoot = path.join(bundleRoot, "app");
const pythonExecutable = path.join(pythonRoot, "bin", "python3");


function run(executable, args, options) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      ...options,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString("utf8"); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
    child.once("error", reject);
    child.once("close", (code) => resolve({ code, stdout, stderr }));
  });
}


async function main() {
  assert.equal(process.platform, "darwin", "bundled Seatbelt supports macOS only");
  assert.equal(process.arch, "arm64", "bundled Seatbelt supports macOS arm64 only");
  assert.ok(!pythonExecutable.includes(".venv"));
  const pythonCheck = String.raw`
import json
from pathlib import Path
import sys
import tempfile

import eidos_runtime
from eidos_runtime.sandbox import seatbelt

python_root = Path(sys.argv[1]).resolve()
app_root = Path(sys.argv[2]).resolve()
assert Path(sys.executable).resolve().is_relative_to(python_root)
assert Path(eidos_runtime.__file__).resolve().is_relative_to(app_root)
assert seatbelt.runtime_python_executable() == Path(sys.executable).resolve()
assert str(seatbelt.runtime_python_executable()) != "/Library/Developer/CommandLineTools/usr/bin/python3"
policy = Path(seatbelt.PROFILE_PATH).read_text(encoding="utf-8")
assert '(deny file-write*\n  (subpath (param "PYTHON_RUNTIME_ROOT"))' in policy
assert "/Library/Developer/CommandLineTools/usr/bin/python3" not in policy

with tempfile.TemporaryDirectory(prefix="eidos-bundled-seatbelt-") as directory:
    workspace = Path(directory)
    source = workspace / "candidate"
    target = workspace / "committed"
    source.write_text("bundled", encoding="utf-8")
    assert seatbelt.secure_workspace_move(workspace, source, target, None) == "committed"
    assert target.read_text(encoding="utf-8") == "bundled"
print(json.dumps({"python": sys.executable, "seatbelt": str(seatbelt.PROFILE_PATH)}))
`;
  const environment = {
    ...process.env,
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
    PYTHONDONTWRITEBYTECODE: "1",
    PYTHONNOUSERSITE: "1",
    PYTHONPATH: appRoot,
  };
  delete environment.EIDOS_PYTHON;
  delete environment.PYTHONHOME;
  const result = await run(
    pythonExecutable,
    ["-c", pythonCheck, pythonRoot, appRoot],
    { cwd: appRoot, env: environment },
  );
  assert.equal(result.code, 0, `${result.stdout}\n${result.stderr}`);
  console.log("Bundled Python secure_workspace_move passed with the active interpreter.");
}


try {
  await main();
} catch (error) {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error));
  process.exitCode = 1;
}
