from __future__ import annotations

import codecs
from dataclasses import dataclass, field
import os
import selectors
import select
import signal
import subprocess
import threading
import time
import uuid

from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResource,
    RuntimeResourceKind,
)
from eidos_runtime.sandbox.shell import (
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_METADATA_BYTES,
    POST_TERMINATION_DRAIN_SECONDS,
    ShellLaunchSpec,
    _process_group_exists,
    _terminate_group,
)


class ShellProcessSessionNotFound(KeyError):
    """Raised when a ToolCall names a session outside its Run."""


@dataclass
class ShellProcessSession:
    session_id: str
    owner_run_id: str
    process: subprocess.Popen[bytes]
    process_group_id: int
    launch: ShellLaunchSpec
    started_at: float
    resource: RuntimeResource | None = None
    stdout_text: str = ""
    stderr_text: str = ""
    stdout_cursor: int = 0
    stderr_cursor: int = 0
    original_bytes: int = 0
    accepted_bytes: int = 0
    truncated: bool = False
    exit_code: int | None = None
    execution_status: str = "running"
    termination: str = "running"
    drain_error: str | None = None
    thread: threading.Thread | None = None
    done: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    write_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def resource_id(self) -> str | None:
        return self.resource.resource_id if self.resource is not None else None

    @property
    def omitted_bytes(self) -> int:
        return min(
            MAX_OUTPUT_METADATA_BYTES,
            max(0, self.original_bytes - self.accepted_bytes),
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
    ) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise RuntimeError("shell process manager is closed")
        try:
            process = subprocess.Popen(
                launch.argv,
                cwd=launch.cwd,
                env=launch.environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            raise RuntimeError("shell_process_start_failed") from error
        session_id = f"shell-{uuid.uuid4()}"
        session = ShellProcessSession(
            session_id=session_id,
            owner_run_id=self.owner_run_id,
            process=process,
            process_group_id=process.pid,
            launch=launch,
            started_at=time.monotonic(),
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
        return self._wait_and_snapshot(session, yield_time_ms, "run_shell")

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

    def write_stdin(
        self,
        session_id: str,
        chars: str = "",
        *,
        yield_time_ms: int = 10_000,
    ) -> dict[str, object]:
        session = self._session(session_id)
        if chars == "\x03":
            with session.lock:
                if session.execution_status == "running":
                    try:
                        os.killpg(session.process_group_id, signal.SIGINT)
                    except (ProcessLookupError, PermissionError):
                        pass
        elif chars:
            self._write(session, chars)
        return self._wait_and_snapshot(session, yield_time_ms, "write_stdin")

    def interrupt(
        self,
        session_id: str,
        *,
        yield_time_ms: int = 10_000,
    ) -> dict[str, object]:
        return self.write_stdin(session_id, "\x03", yield_time_ms=yield_time_ms)

    def cleanup(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions.values())
            self._closed = True
        for session in sessions:
            self._terminate(session)
        for session in sessions:
            thread = session.thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)
            if thread is not None and thread.is_alive():
                _terminate_group(session.process_group_id)
                thread.join(timeout=2.0)
            self._close_pipes(session)
            resource = session.resource
            if resource is not None and resource.diagnostics().state.value != "closed":
                resource.close()
        with self._lock:
            self._sessions.clear()

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
    ) -> dict[str, object]:
        timeout = max(0.0, yield_time_ms / 1000.0)
        session.done.wait(timeout)
        with session.lock:
            stdout = session.stdout_text[session.stdout_cursor:]
            stderr = session.stderr_text[session.stderr_cursor:]
            session.stdout_cursor = len(session.stdout_text)
            session.stderr_cursor = len(session.stderr_text)
            running = session.execution_status == "running"
            data: dict[str, object] = {
                "executionStatus": "running" if running else "exited",
                "exitCode": None if running else session.exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": session.truncated,
                "termination": "running" if running else session.termination,
                "durationMs": max(
                    0, int((time.monotonic() - session.started_at) * 1000)
                ),
                "originalBytes": session.original_bytes,
                "omittedBytes": session.omitted_bytes,
            }
            if running:
                data["sessionId"] = session.session_id
            if session.launch.shell_kind is not None:
                data["shellKind"] = session.launch.shell_kind
            if session.launch.environment_source is not None:
                data["environmentSource"] = session.launch.environment_source
            return {
                "schemaVersion": 1,
                "toolName": tool_name,
                "outcome": "success"
                if running or (session.exit_code == 0 and session.termination == "exit")
                else "error",
                "code": "shell_running"
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
                "reconciliationRequired": False,
            }

    def _write(self, session: ShellProcessSession, chars: str) -> None:
        payload = chars.encode("utf-8")
        if not payload:
            return
        process_stdin = session.process.stdin
        if process_stdin is None:
            raise RuntimeError("shell_stdin_unavailable")
        descriptor = process_stdin.fileno()
        with session.write_lock:
            offset = 0
            deadline = time.monotonic() + 30.0
            os.set_blocking(descriptor, False)
            while offset < len(payload):
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
                    offset += written

    def _terminate(self, session: ShellProcessSession) -> None:
        with session.lock:
            session.stop.set()
            if session.execution_status == "running":
                session.termination = "canceled"
        _terminate_group(session.process_group_id)

    def _drain(self, session: ShellProcessSession) -> None:
        selector = selectors.DefaultSelector()
        decoders = {
            stream: codecs.getincrementaldecoder("utf-8")(errors="replace")
            for stream in ("stdout", "stderr")
        }
        streams = {
            "stdout": session.process.stdout,
            "stderr": session.process.stderr,
        }
        try:
            for name, stream in streams.items():
                if stream is not None:
                    selector.register(stream, selectors.EVENT_READ, name)
            termination_started_at: float | None = None
            while selector.get_map() or session.process.poll() is None:
                ready = selector.select(timeout=0.1) if selector.get_map() else ()
                for key, _mask in ready:
                    try:
                        chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    except OSError as error:
                        with session.lock:
                            session.drain_error = type(error).__name__
                        chunk = b""
                    if not chunk:
                        try:
                            selector.unregister(key.fileobj)
                        except (KeyError, ValueError):
                            pass
                        continue
                    self._capture(session, key.data, chunk, decoders[key.data])
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
        except Exception as error:
            with session.lock:
                session.drain_error = type(error).__name__
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
            selector.close()
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
                except Exception:
                    pass
            session.done.set()

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
            remaining = max(0, MAX_OUTPUT_BYTES - session.accepted_bytes)
            accepted = chunk[:remaining]
            if len(accepted) < len(chunk):
                session.truncated = True
            if not accepted:
                return
            session.accepted_bytes += len(accepted)
            text = decoder.decode(accepted, final=False)
            ShellProcessManager._append_decoded(session, stream, text)

    @staticmethod
    def _append_decoded(
        session: ShellProcessSession, stream: str, text: str
    ) -> None:
        if not text:
            return
        with session.lock:
            if stream == "stdout":
                session.stdout_text += text
            else:
                session.stderr_text += text

    @staticmethod
    def _close_pipes(session: ShellProcessSession) -> None:
        for stream in (session.process.stdin, session.process.stdout, session.process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
