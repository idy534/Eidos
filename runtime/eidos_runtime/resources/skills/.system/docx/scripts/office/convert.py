"""A thin adapter to the bundled, offline LibreOfficeKit Node API."""
import json
import os
from pathlib import Path
import subprocess
import sys


def convert(source, target, *, operation="convert", timeout=120):
    node = os.environ.get("RUNTIME_NODE")
    if not node or not Path(node).is_absolute():
        raise RuntimeError("The bound Node runtime is unavailable")
    output = Path(target).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        node, "--preserve-symlinks", "--preserve-symlinks-main",
        str(Path(__file__).with_name("bridge.mjs")), operation,
        str(Path(source).resolve()), str(output), str(timeout),
    ], capture_output=True, text=True, timeout=timeout + 5)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-4000:])
    if not output.is_file() or not output.stat().st_size:
        raise RuntimeError("The document engine produced no output")
    return json.loads(result.stdout)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: convert.py INPUT OUTPUT (output must not already exist)")
    print(json.dumps(convert(sys.argv[1], sys.argv[2])))
