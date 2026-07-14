from __future__ import annotations

import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from typing import Callable

from eidos_runtime.seatbelt import SeatbeltProfile


MAX_OUTPUT_BYTES = 256 * 1024


def run_shell(
    workspace_root: Path,
    command: str,
    cwd: Path,
    timeout_seconds: int,
    cancel: threading.Event,
    on_delta: Callable[[str], None],
) -> dict[str, object]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="eidos-shell-") as temporary:
        root = Path(temporary)
        home = root / "home"
        temp = root / "tmp"
        home.mkdir()
        temp.mkdir()
        profile = SeatbeltProfile.create(
            workspace_root=workspace_root,
            sandbox_home=home,
            sandbox_tmp=temp,
            sensitive_path=workspace_root / ".env",
        )
        process = subprocess.Popen(
            profile.command(["/bin/sh", "-c", command]),
            cwd=cwd,
            env=profile.environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        outputs = {"stdout": bytearray(), "stderr": bytearray()}
        total = 0
        truncated = False
        termination = "exit"
        try:
            while selector.get_map():
                if cancel.is_set():
                    termination = "canceled"
                    _terminate(process)
                elif time.monotonic() - started >= timeout_seconds:
                    termination = "timeout"
                    _terminate(process)
                for key, _mask in selector.select(timeout=0.1):
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = max(0, MAX_OUTPUT_BYTES - total)
                    accepted = chunk[:remaining]
                    if accepted:
                        outputs[key.data].extend(accepted)
                        total += len(accepted)
                        on_delta(accepted.decode("utf-8", errors="replace"))
                    if len(accepted) < len(chunk):
                        truncated = True
                if process.poll() is not None and not selector.get_map():
                    break
            returncode = process.wait(timeout=1)
        finally:
            selector.close()
            if process.poll() is None:
                _terminate(process)
                process.wait(timeout=1)
            process.stdout.close()
            process.stderr.close()
        stdout = bytes(outputs["stdout"]).decode("utf-8", errors="replace")
        stderr = bytes(outputs["stderr"]).decode("utf-8", errors="replace")
        outcome = "success" if returncode == 0 and termination == "exit" else "error"
        code = "ok" if outcome == "success" else termination if termination != "exit" else "nonzero_exit"
        return {
            "schemaVersion": 1,
            "toolName": "run_shell",
            "outcome": outcome,
            "code": code,
            "summary": "Command completed" if outcome == "success" else "Command did not succeed",
            "data": {
                "exitCode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": truncated,
                "termination": termination,
                "durationMs": int((time.monotonic() - started) * 1000),
            },
            "sideEffectsMayExist": True,
        }


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=0.5)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
