"""A ConPTY attached to a suspended, Job-owned Windows process."""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import msvcrt
import os
from pathlib import Path
import subprocess
import threading
from typing import Sequence
import _winapi

from eidos_runtime.infrastructure.windows_processes import ProcessJob, _api


class _Coord(ctypes.Structure):
    _fields_ = [("columns", ctypes.c_short), ("rows", ctypes.c_short)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD), ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD), ("y_size", wintypes.DWORD),
        ("x_count", wintypes.DWORD), ("y_count", wintypes.DWORD),
        ("fill_attribute", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("show_window", wintypes.WORD), ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.c_void_p), ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
        ("pid", wintypes.DWORD), ("tid", wintypes.DWORD),
    ]


_create_console = _api(
    "CreatePseudoConsole", ctypes.c_long, _Coord, wintypes.HANDLE, wintypes.HANDLE,
    wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
)
_resize_console = _api("ResizePseudoConsole", ctypes.c_long, wintypes.HANDLE, _Coord)
_close_console = _api("ClosePseudoConsole", None, wintypes.HANDLE)
_init_attributes = _api(
    "InitializeProcThreadAttributeList", wintypes.BOOL, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t),
)
_update_attributes = _api(
    "UpdateProcThreadAttribute", wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD,
    ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
)
_delete_attributes = _api("DeleteProcThreadAttributeList", None, ctypes.c_void_p)
_create_process = _api(
    "CreateProcessW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPWSTR,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
    ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInfo),
)
_resume_thread = _api("ResumeThread", wintypes.DWORD, wintypes.HANDLE)

_PSEUDOCONSOLE_ATTRIBUTE = 0x00020016
_CREATION_FLAGS = 0x00000004 | 0x00000400 | 0x00080000  # Suspended, Unicode environment, extended startup.
_USE_STD_HANDLES = 0x00000100  # STARTF_USESTDHANDLES
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _spawn(console: int, argv: Sequence[str], cwd: Path, env: dict[str, str]) -> _ProcessInfo:
    size = ctypes.c_size_t()
    _init_attributes(None, 1, 0, ctypes.byref(size))
    attributes = ctypes.create_string_buffer(size.value)
    if not _init_attributes(attributes, 1, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _update_attributes(
            attributes, 0, _PSEUDOCONSOLE_ATTRIBUTE, console, ctypes.sizeof(wintypes.HANDLE), None, None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = _StartupInfoEx()
        startup.startup.size = ctypes.sizeof(startup)
        # Without explicit standard handles, a console child of a Runtime whose
        # stdio is redirected writes to the Runtime's own stdout (the JSON-RPC
        # channel) instead of the pseudo console. Invalid handles make the
        # child use the attached ConPTY for all three streams.
        startup.startup.flags = _USE_STD_HANDLES
        startup.startup.stdin = _INVALID_HANDLE
        startup.startup.stdout = _INVALID_HANDLE
        startup.startup.stderr = _INVALID_HANDLE
        startup.attributes = ctypes.addressof(attributes)
        process = _ProcessInfo()
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        entries = sorted(env.items(), key=lambda entry: entry[0].upper())
        if any(not key or "=" in key or "\0" in key or "\0" in value for key, value in entries):
            raise ValueError("invalid terminal environment")
        environment = ctypes.create_unicode_buffer("".join(f"{key}={value}\0" for key, value in entries) + "\0")
        if not _create_process(
            argv[0], command, None, None, False, _CREATION_FLAGS, environment, str(cwd),
            ctypes.byref(startup), ctypes.byref(process),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return process
    finally:
        _delete_attributes(attributes)


class ConPtyProcess:
    stderr = None  # ConPTY carries both streams as one UTF-8 terminal stream.

    def __init__(self, argv: Sequence[str], cwd: Path, env: dict[str, str], rows: int, columns: int) -> None:
        self.args = argv
        self.returncode: int | None = None
        self._lock = threading.Lock()
        self._closing: threading.Thread | None = None
        console = wintypes.HANDLE()
        with ExitStack() as setup, ExitStack() as client_pipes:
            self.job = ProcessJob()
            setup.callback(self.job.close)
            input_read, input_write = os.pipe()
            client_pipes.callback(os.close, input_read)
            self.stdin = setup.enter_context(os.fdopen(input_write, "wb", buffering=0))
            output_read, output_write = os.pipe()
            client_pipes.callback(os.close, output_write)
            self.stdout = setup.enter_context(os.fdopen(output_read, "rb", buffering=0))
            result = _create_console(
                _Coord(columns, rows), msvcrt.get_osfhandle(input_read), msvcrt.get_osfhandle(output_write),
                0, ctypes.byref(console),
            )
            if result < 0:
                raise OSError(f"CreatePseudoConsole failed ({result:#x})")
            self._console: int | None = console.value
            try:
                process = _spawn(console.value, argv, cwd, env)
                self._process = subprocess.Handle(process.process)
                self._primary_thread = subprocess.Handle(process.thread)
                self.pid = process.pid
                try:
                    self.job.assign(process.process)
                except OSError:
                    _winapi.TerminateProcess(process.process, 1)
                    raise
            except BaseException:
                client_pipes.close()
                setup.close()
                _close_console(console.value)
                raise
            setup.pop_all()

    def release(self) -> None:
        with self._lock:
            if self._primary_thread.closed:
                return
            try:
                if _resume_thread(self._primary_thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self._primary_thread.Close()

    def wait(self, timeout: float | None = None) -> int:
        milliseconds = _winapi.INFINITE if timeout is None else max(0, int(timeout * 1000))
        if self.returncode is None:
            if _winapi.WaitForSingleObject(self._process, milliseconds) == _winapi.WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(self.args, timeout)
            self.returncode = _winapi.GetExitCodeProcess(self._process)
        return self.returncode

    def poll(self) -> int | None:
        try:
            return self.wait(0)
        except subprocess.TimeoutExpired:
            return None

    def has_members(self) -> bool:
        return self.job.has_members()

    def terminate_tree(self) -> None:
        self.job.terminate()

    def resize(self, rows: int, columns: int) -> None:
        with self._lock:
            if self._console is None:
                raise RuntimeError("shell_session_exited")
            result = _resize_console(self._console, _Coord(columns, rows))
            if result < 0:
                raise OSError(f"ResizePseudoConsole failed ({result:#x})")

    def finish_output(self) -> None:
        with self._lock:
            if self._console is None:
                return
            # Older Windows builds wait for the last screen update to be read.
            # The session drain keeps reading while this owned worker closes ConPTY.
            worker = threading.Thread(target=_close_console, args=(self._console,), name=f"eidos-conpty-{self.pid}")
            worker.start()
            self._closing = worker
            self._console = None

    def close_job(self) -> None:
        self.job.close()
        with self._lock:
            self._primary_thread.Close()
        self.finish_output()
        assert self._closing is not None
        self._closing.join(timeout=2)

    @property
    def quiescent(self) -> bool:
        return self._closing is not None and not self._closing.is_alive()
