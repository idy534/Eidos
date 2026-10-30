"""PIPE and PTY descriptors for the existing Run-owned Shell manager."""
from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import select
import stat
import subprocess
import termios
import threading
import time

from eidos_runtime.sandbox.seatbelt import runtime_python_executable
from eidos_runtime.sandbox.shell import ShellLaunchSpec, _terminate_group


PTY_HELPER = Path(__file__).parents[1] / "sandbox" / "pty_exec_helper.py"


@dataclass
class ShellProcessTransport:
    process: subprocess.Popen[bytes]
    terminal_fd: int | None = None
    stdin_closed: bool = False
    start_fd: int | None = None

    def start_execution(self) -> None:
        if self.start_fd is not None:
            try:
                os.write(self.start_fd, b"g")
            finally:
                os.close(self.start_fd)
                self.start_fd = None

    @property
    def input_fd(self) -> int:
        if self.stdin_closed:
            raise RuntimeError("shell_stdin_closed")
        if self.terminal_fd is not None:
            return self.terminal_fd
        if self.process.stdin is None:
            raise RuntimeError("shell_stdin_unavailable")
        return self.process.stdin.fileno()

    def output_descriptors(self) -> tuple[tuple[str, int], ...]:
        if self.terminal_fd is not None:
            return (("stdout", self.terminal_fd),)
        return tuple(
            (name, stream.fileno())
            for name, stream in (("stdout", self.process.stdout), ("stderr", self.process.stderr))
            if stream is not None
        )

    def resize(self, rows: int, columns: int) -> None:
        if self.terminal_fd is None:
            raise RuntimeError("shell_not_a_terminal")
        termios.tcsetwinsize(self.terminal_fd, (rows, columns))

    def close_stdin(self) -> None:
        if self.terminal_fd is not None:
            raise RuntimeError("shell_terminal_cannot_close_stdin")
        if not self.stdin_closed and self.process.stdin is not None:
            self.process.stdin.close()
        self.stdin_closed = True

    def close(self) -> None:
        if self.start_fd is not None:
            os.close(self.start_fd)
            self.start_fd = None
        if self.terminal_fd is not None:
            os.close(self.terminal_fd)
            self.terminal_fd = None
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass


def spawn_shell_process(
    launch: ShellLaunchSpec, *, cancel: threading.Event | None = None,
) -> ShellProcessTransport:
    if not launch.tty:
        return ShellProcessTransport(subprocess.Popen(
            launch.argv, cwd=launch.cwd, env=launch.environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        ))
    master = slave = error_read = error_write = start_read = start_write = -1
    process: subprocess.Popen[bytes] | None = None
    try:
        master, slave = os.openpty()
        error_read, error_write = os.pipe()
        start_read, start_write = os.pipe()
        device = Path(os.ttyname(slave))
        opened, named = os.fstat(slave), device.lstat()
        if (not stat.S_ISCHR(named.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_uid)
            != (named.st_dev, named.st_ino, named.st_uid)):
            raise OSError(errno.ENODEV, "terminal identity changed")
        termios.tcsetwinsize(slave, (launch.rows, launch.columns))
        argv = launch.terminal_command(device)
        process = subprocess.Popen(
            (str(runtime_python_executable()), "-I", str(PTY_HELPER),
             str(slave), str(error_write), str(start_read), *argv),
            cwd=launch.cwd, env=launch.environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(slave, error_write, start_read),
        )
        os.close(error_write)
        error_write = -1
        os.close(slave)
        slave = -1
        # Acknowledge terminal setup before exposing the process group. The
        # helper cannot execute the command until the manager releases its gate.
        deadline = time.monotonic() + 5.0
        while True:
            if cancel is not None and cancel.is_set():
                raise OSError(errno.ECANCELED, "terminal launch canceled")
            if time.monotonic() >= deadline:
                raise OSError(errno.ETIMEDOUT, "terminal launch timed out")
            if select.select([error_read], [], [], 0.05)[0]:
                if os.read(error_read, 64) != b"\0":
                    raise OSError(errno.EIO, "terminal setup failed")
                break
        os.set_blocking(master, False)
        result = ShellProcessTransport(process, master, start_fd=start_write)
        master = -1
        start_write = -1
        return result
    except BaseException:
        if process is not None:
            _terminate_group(process.pid)
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        if master >= 0:
            os.close(master)
        raise
    finally:
        for descriptor in (slave, error_read, error_write, start_read, start_write):
            if descriptor >= 0:
                os.close(descriptor)
