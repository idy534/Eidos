"""Build an isolated, locked Qoder SDK payload for the managed resource pack."""
from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "resources/runtime-dependencies/qoder"


def build(target: Path, python: Path) -> None:
    controlled = ROOT / "build"
    target = target.resolve()
    if not target.is_relative_to(controlled.resolve()) or target == controlled.resolve():
        raise ValueError("Qoder payload must be inside build/")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="qoder-lock-") as folder:
        requirements = Path(folder) / "requirements.txt"
        subprocess.run(["uv", "export", "--project", str(PROJECT), "--locked", "--no-dev", "--no-emit-project",
                        "--output-file", str(requirements)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["uv", "pip", "install", "--python", str(python), "--target", str(target),
                        "--require-hashes", "--only-binary", ":all:", "--link-mode", "copy",
                        "-r", str(requirements)], check=True)
    shutil.copytree(PROJECT / "src/eidos_qoder_worker", target / "eidos_qoder_worker")
    shutil.copy2(ROOT / "runtime/eidos_runtime/model/qoder_protocol.py", target / "eidos_qoder_worker/protocol.py")
    for cache in target.rglob("__pycache__"):
        shutil.rmtree(cache)
    (target / ".lock").unlink(missing_ok=True)
    cli = target / "qoder_agent_sdk/_bundled/qodercli"
    version = subprocess.check_output([str(cli), "--version"], text=True).strip()
    if "1.1.79" not in version:
        raise ValueError("bundled Qoder CLI version changed")
    versions = {d.metadata["Name"].lower(): d.version for d in importlib.metadata.distributions(path=[str(target)])}
    if versions.get("qoder-agent-sdk") != "1.0.17" or not versions.get("mcp", "").startswith("1."):
        raise ValueError("isolated Qoder dependency lock is invalid")
    print(f"Qoder isolated payload ready: SDK 1.0.17 / CLI {version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=ROOT / "build/qoder-worker/lib")
    parser.add_argument("--python", type=Path, default=ROOT / ".venv/bin/python")
    args = parser.parse_args()
    build(args.target, args.python.resolve())


if __name__ == "__main__":
    main()
