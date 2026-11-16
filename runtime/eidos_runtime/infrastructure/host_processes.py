"""Owned process launches and bounded pipe access on supported hosts."""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import subprocess
import sys
from typing import BinaryIO, Protocol, Sequence


WINDOWS = sys.platform == "win32"

if WINDOWS:
    from eidos_runtime.infrastructure import windows_processes
    from eidos_runtime.infrastructure.windows_terminal import ConPtyProcess


class OwnedProcess(Protocol):
    pid: int
    returncode: int | None
    stdin: BinaryIO | None
    stdout: BinaryIO | None
    stderr: BinaryIO | None

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...


def start(
    argv: Sequence[str], *, cwd: Path | str, env: dict[str, str],
    stdin: int | BinaryIO, stdout: int, stderr: int, hold: bool = False,
) -> subprocess.Popen[bytes]:
    if WINDOWS:
        return windows_processes.JobProcess(
            argv, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr, hold=hold,
        )
    return subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
        start_new_session=True, shell=False, close_fds=True,
    )


def release(process: OwnedProcess) -> None:
    if WINDOWS and isinstance(process, (windows_processes.JobProcess, ConPtyProcess)):
        process.release()


def has_members(process: OwnedProcess) -> bool:
    if WINDOWS and isinstance(process, (windows_processes.JobProcess, ConPtyProcess)):
        return process.has_members()
    raise ValueError("Windows subprocess is missing its Job Object")


def terminate_tree(process: OwnedProcess) -> None:
    if WINDOWS and isinstance(process, (windows_processes.JobProcess, ConPtyProcess)):
        process.terminate_tree()
        return
    raise ValueError("Windows subprocess is missing its Job Object")


def close_job(process: OwnedProcess) -> None:
    if WINDOWS and isinstance(process, (windows_processes.JobProcess, ConPtyProcess)):
        process.close_job()


def pipe_selector():
    if WINDOWS:
        return windows_processes.PipeSelector()
    return selectors.DefaultSelector()


def write_pipe(descriptor: int, data: bytes) -> int:
    if WINDOWS:
        return windows_processes.write_pipe(descriptor, data)
    return os.write(descriptor, data)
