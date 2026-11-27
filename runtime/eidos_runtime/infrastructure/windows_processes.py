"""Windows subprocess ownership and polling for redirected pipes."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import errno
import msvcrt
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time
from typing import BinaryIO, Iterator, Sequence
import _winapi

from eidos_runtime.infrastructure.mxc_status import MAX_MXC_STATUS_BYTES, MxcProcessStatus, parse_mxc_status


_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_HANDLE, _DWORD, _BOOL = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL
_PTR = ctypes.c_void_p
_DWORD_PTR = ctypes.POINTER(_DWORD)


def _api(name: str, result: object, *arguments: object):
    function = getattr(_kernel, name)
    function.restype = result
    function.argtypes = arguments
    return function


_create_job = _api("CreateJobObjectW", _HANDLE, _PTR, wintypes.LPCWSTR)
_set_job = _api("SetInformationJobObject", _BOOL, _HANDLE, wintypes.INT, _PTR, _DWORD)
_query_job = _api("QueryInformationJobObject", _BOOL, _HANDLE, wintypes.INT, _PTR, _DWORD, _DWORD_PTR)
_assign_job = _api("AssignProcessToJobObject", _BOOL, _HANDLE, _HANDLE)
_terminate_job = _api("TerminateJobObject", _BOOL, _HANDLE, wintypes.UINT)
_close = _api("CloseHandle", _BOOL, _HANDLE)
_peek = _api("PeekNamedPipe", _BOOL, _HANDLE, _PTR, _DWORD, _DWORD_PTR, _DWORD_PTR, _DWORD_PTR)
_set_pipe_state = _api("SetNamedPipeHandleState", _BOOL, _HANDLE, _DWORD_PTR, _DWORD_PTR, _DWORD_PTR)
_write_file = _api("WriteFile", _BOOL, _HANDLE, _PTR, _DWORD, _DWORD_PTR, _PTR)
_current_process = _api("GetCurrentProcess", _HANDLE)
_open_process = _api("OpenProcess", _HANDLE, _DWORD, _BOOL, _DWORD)
_times = _api("GetProcessTimes", _BOOL, _HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))


def _start_time(handle: int) -> int:
    values = [wintypes.FILETIME() for _ in range(4)]
    if not _times(handle, *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    started = values[0]
    return (started.dwHighDateTime << 32) | started.dwLowDateTime


def owner_start_time() -> int:
    return _start_time(_current_process())


@contextmanager
def process_owner(pid: int, started: int) -> Iterator[int]:
    handle = _open_process(0x101000, False, pid)  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _start_time(handle) != started or _winapi.WaitForSingleObject(handle, 0) != _winapi.WAIT_TIMEOUT:
            raise ProcessLookupError("MCP Runtime has exited")
        yield handle
    finally:
        _close(handle)


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", _DWORD),
        ("min_working_set", ctypes.c_size_t), ("max_working_set", ctypes.c_size_t),
        ("active_limit", _DWORD), ("affinity", ctypes.c_size_t),
        ("priority", _DWORD), ("schedule", _DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4), ("page_faults", _DWORD), ("total", _DWORD),
        ("active", _DWORD), ("terminated", _DWORD),
    ]


_BOOTSTRAP = Path(__file__).with_name("windows_process_launcher.py")
_PIPE_EOF_ERRORS = frozenset({109, 232, 233})
_PIPE_NOWAIT = 0x00000001
_MAX_MXC_REQUEST_BYTES = 1024 * 1024


class ProcessJob:
    def __init__(self) -> None:
        job = _create_job(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not _set_job(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            _close(job)
            raise error
        self._handle = job
        self._lock = threading.Lock()

    def assign(self, process_handle: int) -> None:
        if not _assign_job(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def has_members(self) -> bool:
        with self._lock:
            if not self._handle:
                return False
            information = _Accounting()
            if not _query_job(self._handle, 1, ctypes.byref(information), ctypes.sizeof(information), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return information.active > 0

    def terminate(self) -> None:
        with self._lock:
            if self._handle and not _terminate_job(self._handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        with self._lock:
            if self._handle:
                _close(self._handle)
                self._handle = 0


class JobProcess(subprocess.Popen[bytes]):
    """The bundled launcher waits until it has been placed in the Job Object."""

    def __init__(
        self, argv: Sequence[str], *, cwd: Path | str, env: dict[str, str],
        stdin: int | BinaryIO | None, stdout: int | None, stderr: int | None, hold: bool = False,
    ) -> None:
        with self._launch_gate() as handle:
            self._start_in_job(
                [sys.executable, "-I", "-B", "-S", "-u", str(_BOOTSTRAP), str(handle), *argv],
                handles=[handle], cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
            )
            if not hold:
                self.release()

    @contextmanager
    def _launch_gate(self, size: int = 0) -> Iterator[int]:
        """Keep the child's gate unreadable until the host has registered it."""
        self.job = ProcessJob()
        self._gate_lock = threading.Lock()
        self._gate: int | None = None
        read_handle = None
        try:
            read_handle, self._gate = _winapi.CreatePipe(None, size)
            yield read_handle
        except BaseException:
            self.close_job()
            if getattr(self, "_child_created", False):
                self.abort_setup()
            raise
        finally:
            if read_handle is not None:
                _winapi.CloseHandle(read_handle)

    def _start_in_job(
        self, argv: Sequence[str], *, handles: list[int], cwd: Path | str, env: dict[str, str],
        stdin: int | BinaryIO | None, stdout: int | None, stderr: int | None,
    ) -> None:
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO(lpAttributeList={"handle_list": handles})
        super().__init__(
            argv, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
            startupinfo=startup, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        # CPython retains this exact process handle for poll/wait. A PID lookup
        # cannot provide the same identity if the launcher died.
        self.job.assign(int(self._handle))

    def abort_setup(self) -> None:
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        if self.poll() is None:
            super().kill()
            self.wait(timeout=2)

    def wait_for_owner_or_exit(self, owner: int) -> int:
        result = _winapi.WaitForMultipleObjects([owner, int(self._handle)], False, _winapi.INFINITE)
        return 1 if result == _winapi.WAIT_OBJECT_0 else self.wait()

    def release(self) -> None:
        with self._gate_lock:
            if self._gate is not None:
                try:
                    _winapi.WriteFile(self._gate, b"\x01")
                finally:
                    self._close_gate()

    def _close_gate(self) -> None:
        if self._gate is not None:
            _winapi.CloseHandle(self._gate)
            self._gate = None

    def has_members(self) -> bool:
        return self.job.has_members()

    def terminate_tree(self) -> None:
        self.job.terminate()

    def close_job(self) -> None:
        with self._gate_lock:
            self._close_gate()
            self.job.close()


class MxcJobProcess(JobProcess):
    """Pass the request and launch status on two pipes separate from workload stdio."""

    def __init__(
        self, argv: Sequence[str], *, cwd: Path | str, env: dict[str, str],
        stdin: int | BinaryIO | None, stdout: int | None, stderr: int | None,
        request: bytes, launch_resources: ExitStack | None = None,
    ) -> None:
        if not request or len(request) > _MAX_MXC_REQUEST_BYTES:
            raise ValueError("MXC request must fit in a 1 MiB pipe")
        self._request = request
        self._release_attempted = False
        self._status_read: int | None = None
        self._status: MxcProcessStatus | None = None
        self._launch_resources: ExitStack | None = None
        status_read, status_write = _winapi.CreatePipe(None, MAX_MXC_STATUS_BYTES)
        try:
            with self._launch_gate(len(request)) as request_read:
                mode = _DWORD(_PIPE_NOWAIT)
                if not _set_pipe_state(self._gate, ctypes.byref(mode), None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._start_in_job(
                    [*argv, "--request-handle", str(request_read), "--status-handle", str(status_write)],
                    handles=[request_read, status_write], cwd=cwd, env=env,
                    stdin=stdin, stdout=stdout, stderr=stderr,
                )
            self._status_read = status_read
            # Ownership transfers after the constructor has successfully launched the held helper.
            self._launch_resources = launch_resources
        except BaseException:
            _winapi.CloseHandle(status_read)
            raise
        finally:
            _winapi.CloseHandle(status_write)

    def release(self) -> None:
        with self._gate_lock:
            if self._gate is None:
                return
            self._release_attempted = True
            try:
                # CreatePipe's capacity is advisory. If there is a short write,
                # stop the job before sending EOF to the helper.
                try:
                    written, error = _winapi.WriteFile(self._gate, self._request)
                except OSError:
                    written, error = 0, 1
                if error or written != len(self._request):
                    self.job.terminate()
            finally:
                self._request = b""
                self._close_gate()

    def mxc_status(self) -> MxcProcessStatus:
        with self._gate_lock:
            returncode = self.poll()
            if returncode is None:
                raise RuntimeError("MXC launcher is still running")
            return self._finish_status(returncode)

    def _finish_status(self, returncode: int | None) -> MxcProcessStatus:
        if self._status is None:
            if not self._release_attempted:
                self._status = MxcProcessStatus(failure_code="not_released")
            elif returncode is None or self._status_read is None:
                self._status = MxcProcessStatus()
            else:
                try:
                    payload = _read_mxc_status(self._status_read)
                except OSError:
                    payload = b""
                self._status = parse_mxc_status(payload, returncode)
        return self._status

    def close_job(self) -> None:
        super().close_job()
        with self._gate_lock:
            if self._status_read is not None:
                self._finish_status(self.poll())
                _winapi.CloseHandle(self._status_read)
                self._status_read = None
            self._request = b""
            resources, self._launch_resources = self._launch_resources, None
        if resources is not None:
            resources.close()


def _read_mxc_status(handle: int) -> bytes:
    """Read at most one pipe buffer and require the helper's writer to be closed."""
    pending = _DWORD()
    if not _peek(handle, None, 0, None, ctypes.byref(pending), None):
        return b""
    size = pending.value
    if not 0 < size <= MAX_MXC_STATUS_BYTES:
        return b""
    payload, error = _winapi.ReadFile(handle, size)
    if error or len(payload) != size:
        return b""
    # Reject a valid-looking prefix if a writer survived the launcher.
    if _peek(handle, None, 0, None, None, None) or ctypes.get_last_error() not in _PIPE_EOF_ERRORS:
        return b""
    return payload


def write_pipe(descriptor: int, data: bytes) -> int:
    written = _DWORD()
    packet = data[:16384]
    if _write_file(msvcrt.get_osfhandle(descriptor), packet, len(packet), ctypes.byref(written), None):
        if written.value:
            return written.value
        raise BlockingIOError(errno.EAGAIN, "pipe buffer is full")
    error = ctypes.get_last_error()
    if error == 232:
        raise BlockingIOError(errno.EAGAIN, "pipe buffer is full")
    if error in {109, 233}:
        raise BrokenPipeError(errno.EPIPE, "pipe closed")
    raise ctypes.WinError(error)


class PipeSelector:
    """Wait for reads on the anonymous pipes used by the bundled Python."""

    def __init__(self) -> None:
        self._keys: dict[int, selectors.SelectorKey] = {}

    def register(self, file: BinaryIO | int, events: int, data: str) -> None:
        descriptor = file if isinstance(file, int) else file.fileno()
        self._keys[descriptor] = selectors.SelectorKey(file, descriptor, events, data)

    def unregister(self, file: BinaryIO | int) -> None:
        descriptor = file if isinstance(file, int) else file.fileno()
        del self._keys[descriptor]

    def get_map(self) -> dict[int, selectors.SelectorKey]:
        return self._keys

    def close(self) -> None:
        self._keys.clear()

    def select(self, timeout: float) -> list[tuple[selectors.SelectorKey, int]]:
        deadline = time.monotonic() + timeout
        while True:
            ready: list[tuple[selectors.SelectorKey, int]] = []
            for key in self._keys.values():
                if key.events == selectors.EVENT_WRITE:
                    ready.append((key, key.events))
                elif _readable(key.fd):
                    ready.append((key, key.events))
            left = deadline - time.monotonic()
            if ready or left <= 0:
                return ready
            time.sleep(min(0.01, left))


def _readable(descriptor: int) -> bool:
    pending = _DWORD()
    if _peek(msvcrt.get_osfhandle(descriptor), None, 0, None, ctypes.byref(pending), None):
        return pending.value > 0
    error = ctypes.get_last_error()
    if error in _PIPE_EOF_ERRORS:
        return True
    raise ctypes.WinError(error)
