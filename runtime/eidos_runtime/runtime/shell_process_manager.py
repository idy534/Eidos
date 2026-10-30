from __future__ import annotations

import codecs
from collections.abc import Callable
from dataclasses import dataclass, field
import errno
import os
import selectors
import select
import signal
import subprocess
import threading
import time
import uuid
import logging

from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResource,
    RuntimeResourceKind,
    ResourceRegistryError,
)
from eidos_runtime.sandbox.shell import (
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_METADATA_BYTES,
    POST_TERMINATION_DRAIN_SECONDS,
    ShellLaunchSpec,
    ShellProcessStartError,
    _process_group_exists,
    _terminate_group,
)
from eidos_runtime.sandbox.sensitive import SensitiveScanner, StreamingSensitiveScanner, SensitiveScanError
from eidos_runtime.runtime.shell_output_buffer import ShellOutputBuffer
from eidos_runtime.runtime.shell_process_transport import ShellProcessTransport, spawn_shell_process


class ShellProcessSessionNotFound(KeyError):
    """Raised when a ToolCall names a session outside its Run."""


class ShellSessionFinalizationError(RuntimeError):
    """The process stopped but its final result could not be committed."""


@dataclass
class ShellProcessSession:
    session_id: str
    owner_run_id: str
    process: subprocess.Popen[bytes]
    process_group_id: int
    launch: ShellLaunchSpec
    started_at: float
    resource: RuntimeResource | None = None
    transport: ShellProcessTransport | None = None
    output: dict[str, ShellOutputBuffer] = field(default_factory=lambda: {
        name: ShellOutputBuffer(MAX_OUTPUT_BYTES) for name in ("stdout", "stderr")
    })
    stdout_cursor: int = 0
    stderr_cursor: int = 0
    original_bytes: int = 0
    delta_bytes: int = 0
    columns: int | None = None
    rows: int | None = None
    exit_code: int | None = None
    execution_status: str = "running"
    termination: str = "running"
    drain_error: str | None = None
    thread: threading.Thread | None = None
    done: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    write_lock: threading.Lock = field(default_factory=threading.Lock)
    scanners: dict[str, StreamingSensitiveScanner] = field(default_factory=dict)
    on_output: Callable[[str], None] | None = None
    on_exit: Callable[[dict[str, object]], dict[str, object]] | None = None
    terminal_result: dict[str, object] | None = None
    committed: bool = False
    finalized: bool = False
    observer_lock: threading.RLock = field(default_factory=threading.RLock)
    finalization_error: Exception | None = None

    @property
    def stdout_text(self) -> str:
        return self.output["stdout"].read().text

    @property
    def stderr_text(self) -> str:
        return self.output["stderr"].read().text

    @property
    def truncated(self) -> bool:
        return any(buffer.omitted_bytes for buffer in self.output.values())

    @property
    def resource_id(self) -> str | None:
        return self.resource.resource_id if self.resource is not None else None

    @property
    def omitted_bytes(self) -> int:
        return min(
            MAX_OUTPUT_METADATA_BYTES,
            max(0, self.original_bytes - sum(buffer.retained_bytes for buffer in self.output.values())),
        )


class ShellProcessManager:
    """Owns Run-scoped shell processes and their bounded output drains."""

    def __init__(
        self,
        resource_registry: ResourceRegistry | None = None,
        *,
        owner_run_id: str = "shell",
    ) -> None:
        self.resources = resource_registry or ResourceRegistry()
        self.owner_run_id = owner_run_id
        self._sessions: dict[str, ShellProcessSession] = {}
        self._lock = threading.RLock()
        self._closed = False

    def start(
        self,
        launch: ShellLaunchSpec,
        *,
        yield_time_ms: int = 10_000,
        wait_for_exit: bool = False,
        on_output: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
        sensitive: SensitiveScanner | None = None,
        on_started: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise RuntimeError("shell process manager is closed")
        try:
            transport = spawn_shell_process(launch, cancel=cancel)
            process = transport.process
        except (OSError, ValueError) as error:
            raise ShellProcessStartError("shell_process_start_failed") from error
        session_id = f"shell-{uuid.uuid4()}"
        session = ShellProcessSession(
            session_id=session_id,
            owner_run_id=self.owner_run_id,
            process=process,
            process_group_id=process.pid,
            launch=launch,
            started_at=time.monotonic(),
            transport=transport,
            columns=launch.columns if launch.tty else None,
            rows=launch.rows if launch.tty else None,
        )
        try:
            session.on_output = on_output
            if sensitive is not None:
                for name in ("stdout", "stderr"):
                    session.scanners[name] = StreamingSensitiveScanner(
                        sensitive,
                        on_safe_text=lambda text, stream=name: self._append_safe(session, stream, text),
                    )
            resource = self.resources.register(
                RuntimeResourceKind.SHELL_PROCESS,
                owner_id=self.owner_run_id,
                resource_id=session_id,
                cancel=lambda: self._terminate(session),
                close=lambda: session.stop.set(),
                wait=lambda timeout: session.done.wait(timeout),
                is_quiescent=lambda: session.execution_status == "exited"
                and not _process_group_exists(session.process_group_id),
            )
            session.resource = resource
            with self._lock:
                self._sessions[session_id] = session
            resource.start()
            thread = threading.Thread(
                target=self._drain,
                args=(session,),
                name=f"eidos-shell-{session_id}",
                daemon=False,
            )
            session.thread = thread
            thread.start()
            if on_started is not None:
                on_started()
            if cancel is not None and cancel.is_set():
                self._terminate(session)
            else:
                transport.start_execution()
        except Exception:
            self._terminate(session)
            try:
                process.wait(timeout=2)
            except (subprocess.SubprocessError, OSError):
                with self._lock:
                    self._sessions[session_id] = session
            else:
                self._close_pipes(session)
                session.execution_status = "exited"
                session.exit_code = process.returncode
                session.done.set()
                if session.resource is not None:
                    session.resource.close()
                with self._lock:
                    self._sessions.pop(session_id, None)
            raise
        snapshot = self._wait_and_snapshot(
            session,
            yield_time_ms,
            "run_shell",
            cancel=cancel,
        )
        if cancel is not None and cancel.is_set():
            self._terminate(session)
            session.done.wait(2)
            snapshot = self._wait_and_snapshot(session, 0, "run_shell", cumulative=True)
        if not wait_for_exit:
            return snapshot
        return self._wait_until_exit(session, snapshot, on_output, cancel)

    def wait(
        self,
        session_id: str,
        *,
        yield_time_ms: int = 10_000,
        tool_name: str = "write_stdin",
    ) -> dict[str, object]:
        session = self._session(session_id)
        return self._wait_and_snapshot(session, yield_time_ms, tool_name)

    def poll(self, session_id: str) -> dict[str, object]:
        return self.wait(session_id, yield_time_ms=0)

    def read_output(self, session_id: str) -> dict[str, object]:
        """Observe bounded cumulative output without consuming wait cursors."""
        return self._wait_and_snapshot(self._session(session_id), 0, "run_shell", cumulative=True)

    def write_stdin(
        self,
        session_id: str,
        chars: str = "",
        *,
        yield_time_ms: int = 10_000,
        cancel: threading.Event | None = None,
        on_input: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        session = self._session(session_id)
        if chars == "\x03" and not session.launch.tty:
            with session.lock:
                if session.execution_status == "running":
                    if on_input is not None:
                        on_input()
                    try:
                        os.killpg(session.process_group_id, signal.SIGINT)
                    except (ProcessLookupError, PermissionError):
                        pass
        elif chars:
            self._write(session, chars, cancel=cancel, on_input=on_input)
        if chars == "\x03" and not session.launch.tty:
            if not session.done.wait(min(yield_time_ms / 1000, 1.0)):
                self._terminate(session)
                if not session.done.wait(2):
                    session.drain_error = "process_termination_failed"
            yield_time_ms = 0
        result = self._wait_and_snapshot(session, yield_time_ms, "write_stdin", cancel=cancel)
        self._finalize(session)
        if session.finalization_error is not None:
            raise ShellSessionFinalizationError("shell_result_commit_failed") from session.finalization_error
        if session.terminal_result is not None:
            data = result["data"]
            result = {**session.terminal_result, "toolName": "write_stdin", "data": {**session.terminal_result["data"], "stdout": data["stdout"], "stderr": data["stderr"]}}
        return result

    def control(
        self, session_id: str, action: str, *, columns: int | None = None,
        rows: int | None = None, yield_time_ms: int = 250,
        cancel: threading.Event | None = None,
        on_input: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        session = self._session(session_id)
        if cancel is not None and cancel.is_set():
            raise RuntimeError("shell_control_canceled")
        transport = session.transport
        assert transport is not None
        with session.write_lock:
            if action == "terminate":
                if session.execution_status == "running":
                    if on_input is not None:
                        on_input()
                    self._terminate(session, termination="terminated")
            else:
                if session.execution_status != "running" or session.process.poll() is not None:
                    raise RuntimeError("shell_session_exited")
                if action == "resize":
                    if columns is None or rows is None or not (2 <= columns <= 500 and 2 <= rows <= 500):
                        raise RuntimeError("shell_invalid_terminal_size")
                    if transport.terminal_fd is None:
                        raise RuntimeError("shell_not_a_terminal")
                    if on_input is not None:
                        on_input()
                    transport.resize(rows, columns)
                    with session.lock:
                        session.columns, session.rows = columns, rows
                elif action == "close_stdin":
                    if transport.terminal_fd is not None:
                        raise RuntimeError("shell_terminal_cannot_close_stdin")
                    if on_input is not None and not transport.stdin_closed:
                        on_input()
                    transport.close_stdin()
                else:
                    raise RuntimeError("shell_invalid_control")
        result = self.write_stdin(session_id, yield_time_ms=yield_time_ms, cancel=cancel)
        result["toolName"] = "control_shell"
        result["data"]["controlAction"] = action
        return result

    def require_session(self, session_id: str) -> None:
        self._session(session_id)

    def observe_exit(self, session_id: str, callback: Callable[[dict[str, object]], dict[str, object]]) -> None:
        session = self._session(session_id)
        with session.observer_lock:
            session.on_exit = callback

    def commit(self, session_id: str) -> None:
        session = self._session(session_id)
        with session.observer_lock:
            session.committed = True
        self._finalize(session)
        if session.finalization_error is not None:
            raise ShellSessionFinalizationError("shell_result_commit_failed") from session.finalization_error

    def is_running(self, session_id: str) -> bool:
        try:
            session = self._session(session_id)
        except ShellProcessSessionNotFound:
            return False
        return session.execution_status == "running"

    def has_running(self) -> bool:
        with self._lock:
            return any(session.execution_status == "running" for session in self._sessions.values())

    def _finalize(self, session: ShellProcessSession) -> None:
        with session.observer_lock:
            if not session.committed or session.finalized or session.on_exit is None or not session.done.is_set():
                return
            try:
                session.terminal_result = session.on_exit(self._wait_and_snapshot(session, 0, "run_shell", cumulative=True))
                session.finalized = True
                session.finalization_error = None
            except Exception as error:
                session.finalization_error = error
                logging.getLogger("eidos.runtime").exception("Shell result commit failed")

    def interrupt(
        self,
        session_id: str,
        *,
        yield_time_ms: int = 10_000,
    ) -> dict[str, object]:
        return self.write_stdin(session_id, "\x03", yield_time_ms=yield_time_ms)

    def cleanup(self, *, close: bool = True) -> bool:
        with self._lock:
            sessions = tuple(self._sessions.values())
            if close:
                self._closed = True
        stopped = False
        for session in sessions:
            stopped = self._terminate(session) or stopped
        for session in sessions:
            thread = session.thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)
            if thread is not None and thread.is_alive():
                _terminate_group(session.process_group_id)
                thread.join(timeout=2.0)
            self._close_pipes(session)
            resource = session.resource
            quiescent = session.done.is_set() and not _process_group_exists(session.process_group_id)
            if resource is not None and quiescent and resource.diagnostics().state.value != "closed":
                resource.close()
            self._finalize(session)
            if not quiescent:
                session.finalization_error = ShellSessionFinalizationError("shell_process_still_running")
        with self._lock:
            self._sessions = {session.session_id: session for session in sessions if session.finalization_error is not None}
        if any(session.finalization_error is not None for session in sessions):
            raise ShellSessionFinalizationError("shell_result_commit_failed")
        return stopped

    close = cleanup

    def _session(self, session_id: str) -> ShellProcessSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or session.owner_run_id != self.owner_run_id:
            raise ShellProcessSessionNotFound(session_id)
        return session

    def _wait_and_snapshot(
        self,
        session: ShellProcessSession,
        yield_time_ms: int,
        tool_name: str,
        *,
        cancel: threading.Event | None = None,
        cumulative: bool = False,
    ) -> dict[str, object]:
        timeout = max(0.0, yield_time_ms / 1000.0)
        if cancel is None:
            session.done.wait(timeout)
        else:
            deadline = time.monotonic() + timeout
            while not session.done.is_set() and not cancel.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                session.done.wait(min(0.1, remaining))
        with session.lock:
            stdout = session.output["stdout"].read(0 if cumulative else session.stdout_cursor)
            stderr = session.output["stderr"].read(0 if cumulative else session.stderr_cursor)
            if not cumulative:
                session.stdout_cursor = stdout.next_offset
                session.stderr_cursor = stderr.next_offset
            running = session.execution_status == "running"
            data: dict[str, object] = {
                "executionStatus": "running" if running else "exited",
                "exitCode": None if running else session.exit_code,
                "stdout": stdout.text,
                "stderr": stderr.text,
                "transport": "pty" if session.launch.tty else "pipe",
                "outputMerged": session.launch.tty,
                "outputRevision": min(MAX_OUTPUT_METADATA_BYTES, session.original_bytes * 2 + int(not running)),
                "truncated": session.truncated,
                "termination": "running" if running else session.termination,
                "durationMs": max(
                    0, int((time.monotonic() - session.started_at) * 1000)
                ),
                "originalBytes": session.original_bytes,
                "omittedBytes": session.omitted_bytes,
                "outputComplete": not running and session.drain_error is None and not session.truncated,
                "outputCaptureError": session.drain_error,
                "sessionId": session.session_id,
                "workspaceChanged": False,
                "workspaceChangeState": "unknown",
            }
            if session.launch.shell_kind is not None:
                data["shellKind"] = session.launch.shell_kind
            if session.launch.environment_source is not None:
                data["environmentSource"] = session.launch.environment_source
            if session.launch.tty:
                data.update(columns=session.columns, rows=session.rows)
            return {
                "schemaVersion": 1,
                "toolName": tool_name,
                "outcome": "success"
                if session.drain_error is None and (running or (session.exit_code == 0 and session.termination == "exit"))
                else "error",
                "code": "output_capture_failed" if session.drain_error is not None else "shell_running"
                if running
                else "ok"
                if session.exit_code == 0 and session.termination == "exit"
                else "shell_exit_nonzero"
                if session.termination == "exit"
                else session.termination,
                "summary": "Command is still running"
                if running
                else "Command completed"
                if session.exit_code == 0 and session.termination == "exit"
                else f"Command did not succeed (termination={session.termination})",
                "data": data,
                "sideEffectsMayExist": True,
                "reconciliationRequired": (
                    session.drain_error in {"output_persistence_failed", "process_termination_failed"}
                ) or (not running and session.exit_code is None),
            }

    def _wait_until_exit(
        self,
        session: ShellProcessSession,
        snapshot: dict[str, object],
        on_output: Callable[[str], None] | None,
        cancel: threading.Event | None,
    ) -> dict[str, object]:
        cancel_sent = False
        while True:
            data = snapshot.get("data")
            if not isinstance(data, dict):
                return snapshot
            if data.get("executionStatus") != "running":
                with session.lock:
                    data["stdout"] = session.stdout_text
                    data["stderr"] = session.stderr_text
                return snapshot
            if cancel is not None and cancel.is_set() and not cancel_sent:
                self._terminate(session)
                cancel_sent = True
            snapshot = self._wait_and_snapshot(
                session,
                250,
                "run_shell",
                cancel=None if cancel_sent else cancel,
            )

    def _write(
        self, session: ShellProcessSession, chars: str,
        *, cancel: threading.Event | None = None,
        on_input: Callable[[], None] | None = None,
    ) -> None:
        payload = chars.encode("utf-8")
        if not payload:
            return
        if session.execution_status != "running" or session.process.poll() is not None:
            raise RuntimeError("shell_stdin_closed")
        assert session.transport is not None
        with session.write_lock:
            descriptor = session.transport.input_fd
            offset = 0
            deadline = time.monotonic() + 30.0
            os.set_blocking(descriptor, False)
            while offset < len(payload):
                if session.stop.is_set() or (cancel is not None and cancel.is_set()):
                    raise RuntimeError("shell_stdin_canceled")
                try:
                    written = os.write(descriptor, payload[offset:])
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("shell_stdin_write_timeout")
                    _readable, writable, _exceptional = select.select(
                        [], [descriptor], [], min(0.1, remaining)
                    )
                    if not writable:
                        continue
                except (BrokenPipeError, OSError) as error:
                    raise RuntimeError("shell_stdin_write_failed") from error
                else:
                    if written <= 0:
                        raise RuntimeError("shell_stdin_write_failed")
                    if offset == 0 and on_input is not None:
                        on_input()
                    offset += written

    def _terminate(self, session: ShellProcessSession, *, termination: str = "canceled") -> bool:
        with session.lock:
            session.stop.set()
            stopped = session.process.poll() is None or _process_group_exists(session.process_group_id)
            if stopped and session.execution_status == "running":
                session.termination = termination
        _terminate_group(session.process_group_id)
        return stopped

    def _drain(self, session: ShellProcessSession) -> None:
        selector = selectors.DefaultSelector()
        decoders = {
            stream: codecs.getincrementaldecoder("utf-8")(errors="replace")
            for stream in ("stdout", "stderr")
        }
        assert session.transport is not None
        try:
            for name, descriptor in session.transport.output_descriptors():
                selector.register(descriptor, selectors.EVENT_READ, name)
            termination_started_at: float | None = None
            while selector.get_map() or session.process.poll() is None:
                ready = selector.select(timeout=0.1) if selector.get_map() else ()
                for key, _mask in ready:
                    try:
                        chunk = os.read(key.fd, 64 * 1024)
                    except BlockingIOError:
                        continue
                    except OSError as error:
                        with session.lock:
                            if session.drain_error is None and not (session.launch.tty and error.errno == errno.EIO):
                                session.drain_error = "output_read_failed"
                        chunk = b""
                    if not chunk:
                        try:
                            selector.unregister(key.fileobj)
                        except (KeyError, ValueError):
                            pass
                        continue
                    self._capture(session, key.data, chunk, decoders[key.data])
                if not selector.get_map() and session.process.poll() is None:
                    session.stop.wait(0.01)
                if session.process.poll() is not None:
                    with session.lock:
                        if session.exit_code is None:
                            session.exit_code = session.process.returncode
                            if session.termination == "running":
                                session.termination = "exit"
                    if _process_group_exists(session.process_group_id):
                        with session.lock:
                            if session.termination == "exit":
                                session.termination = "background_process"
                        if termination_started_at is None:
                            termination_started_at = time.monotonic()
                            _terminate_group(session.process_group_id)
                    elif not selector.get_map():
                        break
                if (
                    termination_started_at is not None
                    and time.monotonic() - termination_started_at
                    >= POST_TERMINATION_DRAIN_SECONDS
                ):
                    for key in tuple(selector.get_map().values()):
                        try:
                            selector.unregister(key.fileobj)
                        except (KeyError, ValueError):
                            pass
                    break
            try:
                session.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                _terminate_group(session.process_group_id)
                session.process.wait(timeout=1)
        except Exception:
            with session.lock:
                session.drain_error = "process_drain_failed"
            _terminate_group(session.process_group_id)
            try:
                session.process.wait(timeout=1)
            except (subprocess.SubprocessError, OSError):
                pass
        finally:
            for stream, decoder in decoders.items():
                self._append_decoded(
                    session, stream, decoder.decode(b"", final=True)
                )
            for scanner in session.scanners.values():
                try:
                    scanner.finish()
                except SensitiveScanError:
                    session.drain_error = "sensitive_content_rejected"
            with session.lock:
                for buffer in session.output.values():
                    buffer.finished = True
            selector.close()
            session.stop.set()
            self._close_pipes(session)
            with session.lock:
                if session.exit_code is None:
                    session.exit_code = session.process.returncode
                if session.termination == "running":
                    session.termination = "exit"
                session.execution_status = "exited"
            resource = session.resource
            if resource is not None:
                try:
                    resource.close()
                except ResourceRegistryError as error:
                    if str(error) == "RUNTIME_RESOURCE_NOT_QUIESCENT":
                        session.drain_error = "process_termination_failed"
                except Exception:
                    pass
            session.done.set()
            self._finalize(session)

    @staticmethod
    def _capture(
        session: ShellProcessSession,
        stream: str,
        chunk: bytes,
        decoder,
    ) -> None:
        with session.lock:
            session.original_bytes = min(
                MAX_OUTPUT_METADATA_BYTES,
                session.original_bytes + len(chunk),
            )
            if stream not in session.scanners:
                session.output[stream].append(chunk)
            text = decoder.decode(chunk, final=False)
            ShellProcessManager._append_decoded(session, stream, text)

    @staticmethod
    def _append_decoded(
        session: ShellProcessSession, stream: str, text: str
    ) -> None:
        if not text:
            return
        scanner = session.scanners.get(stream)
        if scanner is not None:
            try:
                scanner.feed(text)
            except SensitiveScanError:
                session.drain_error = "sensitive_content_rejected"
            return
        ShellProcessManager._append_safe(session, stream, text)

    @staticmethod
    def _append_safe(session: ShellProcessSession, stream: str, text: str) -> None:
        with session.lock:
            if stream in session.scanners:
                session.output[stream].append(text.encode("utf-8"))
            # Persist only a bounded live preview. Polls and the final result
            # carry the latest retained tail without unbounded delta events.
            remaining = max(0, MAX_OUTPUT_BYTES - session.delta_bytes)
            preview = text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
            session.delta_bytes += len(preview.encode("utf-8"))
        if preview and session.on_output is not None:
            try:
                session.on_output(preview)
            except Exception:
                session.drain_error = "output_persistence_failed"
                logging.getLogger("eidos.runtime").exception("Shell output persistence failed")

    @staticmethod
    def _close_pipes(session: ShellProcessSession) -> None:
        if session.transport is not None:
            with session.write_lock:
                session.transport.close()
            return
        for stream in (session.process.stdin, session.process.stdout, session.process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
