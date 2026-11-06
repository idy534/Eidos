"""Bounded stdio transport for a Model Lease's isolated Qoder worker."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import anyio
from anyio.abc import Process
from anyio.streams.buffered import BufferedByteReceiveStream

from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.model.client import CreditsUsage, ModelRequestError, ModelRequestFailure, ModelUsage
from eidos_runtime.model.qoder_protocol import (
    MAX_FRAME_BYTES, PACK_ID, RESOURCE_VERSION, WorkerError, WorkerEvent, WorkerRequest, WorkerText,
)

# -I -S excludes user/global site packages and all Runtime dependencies.
BOOTSTRAP = "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('eidos_qoder_worker',run_name='__main__')"


def has_saved_qoder_login(directory: Path) -> bool:
    """Check SDK 1.0.17's auth locations without reading credential contents.

    This is only a hint to avoid connecting an unauthenticated CLI. The SDK
    must still validate the saved login when reading the account's catalog.
    """
    try:
        oauth = directory / "oauth_creds.json"
        if not oauth.is_symlink() and oauth.is_file() and oauth.stat().st_size:
            return True
        auth = directory / ".auth"
        if not auth.is_symlink() and auth.is_dir():
            with os.scandir(auth) as entries:
                return next(entries, None) is not None
        return False
    except OSError:
        raise failure("authentication_failed", "qoder_login_state_unreadable") from None


class QoderWorkerBinding:
    def __init__(self, manager: ManagedResourceManager, config_directory: Path) -> None:
        if sys.version_info[:2] != (3, 12):
            raise ResourceError("resource_python_abi_incompatible", "Qoder SDK requires the App's Python 3.12 runtime")
        catalog = manager.verified_catalog(PACK_ID)
        binding = catalog.resolve(None)
        catalog.verify_binding(binding)
        self.version = manager.pack(PACK_ID).version
        if self.version != RESOURCE_VERSION:
            raise ResourceError("resource_not_ready", "Qoder resource version does not match this adapter")
        self.library = Path(binding.python_path[0])
        self.cli = next((Path(e.path) for e in binding.executables if e.name == "qodercli"), None)
        if self.cli is None or not (self.library / "eidos_qoder_worker/__main__.py").is_file():
            raise ResourceError("resource_not_ready", "Qoder SDK resource is incomplete")
        self.config_directory = config_directory

    @property
    def command(self) -> list[str]:
        return [sys.executable, "-I", "-S", "-c", BOOTSTRAP, str(self.library), str(self.cli), str(self.config_directory)]


def failure(code: str, reason: str, *, progress: bool = False, usage: ModelUsage | None = None) -> ModelRequestError:
    return ModelRequestError(ModelRequestFailure(
        code=code, reason=reason, provider_name="qoder", had_progress=progress, usage=usage,
        # The public SDK doesn't expose a verified internal-retry switch. Keep
        # outer automatic retries off for this adapter to avoid multiplying cost.
        retryable=False,
    ))


class QoderModelDriver:
    """The owning lease closes this worker before releasing its resource."""

    def __init__(self, binding: QoderWorkerBinding) -> None:
        self.binding = binding
        self._process: Process | None = None
        self._output: BufferedByteReceiveStream | None = None
        self._lock = anyio.Lock()
        self._stop_lock = anyio.Lock()
        self._closed = False

    async def _start(self) -> None:
        if self._process is not None:
            return
        root = self.binding.config_directory
        if root.is_symlink() or root.resolve() != root.absolute():
            raise failure("authentication_failed", "qoder_config_directory_unsafe")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if root.is_symlink() or root.stat().st_uid != os.getuid():
            raise failure("authentication_failed", "qoder_config_directory_unsafe")
        os.chmod(root, 0o700)
        self._process = await anyio.open_process(
            self.binding.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True, cwd=root,
            # A model transport needs network, but no inherited Eidos secrets.
            env={key: value for key, value in os.environ.items()
                 if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
                            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                            "http_proxy", "https_proxy", "all_proxy", "no_proxy"}},
        )
        assert self._process.stdout is not None
        self._output = BufferedByteReceiveStream(self._process.stdout)

    async def request(self, request: WorkerRequest, cancel: threading.Event,
                      on_text: Callable[[str], None], timeout: float) -> WorkerEvent:
        data = request.model_dump_json(by_alias=True).encode() + b"\n"
        if len(data) > MAX_FRAME_BYTES or len(request.prompt.encode()) + len(request.instructions.encode()) >= 1024 * 1024:
            raise failure("context_exceeded", "qoder_request_too_large")
        if cancel.is_set():
            raise failure("sampling_canceled", "qoder_canceled")
        async with self._lock:
            if self._closed:
                raise failure("sampling_canceled", "qoder_lease_closed")
            progress = False
            try:
                with anyio.fail_after(timeout):
                    await self._start()
                    assert self._process is not None and self._process.stdin is not None
                    assert self._output is not None
                    async with anyio.create_task_group() as tasks:
                        tasks.start_soon(self._watch_cancel, cancel, tasks.cancel_scope)
                        await self._process.stdin.send(data)
                        while True:
                            line = await self._output.receive_until(b"\n", MAX_FRAME_BYTES)
                            event = WorkerEvent.model_validate_json(line)
                            if event.request_id != request.request_id:
                                raise failure("protocol_error", "qoder_request_identity_mismatch", progress=progress)
                            if isinstance(event.payload, WorkerText):
                                progress = True
                                on_text(event.payload.text)
                                continue
                            tasks.cancel_scope.cancel()
                            break
                    if cancel.is_set() or self._closed:
                        raise failure("sampling_canceled", "qoder_canceled", progress=progress)
                    if isinstance(event.payload, WorkerError):
                        raise failure(event.payload.code, event.payload.reason, progress=progress,
                            usage=ModelUsage(credits_usage=CreditsUsage(amount=event.payload.credits, source="request"))
                            if event.payload.credits is not None else None)
                    return event
            except BaseException as error:
                while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
                    error = error.exceptions[0]
                with anyio.CancelScope(shield=True):
                    await self._stop()
                if cancel.is_set() or self._closed:
                    raise failure("sampling_canceled", "qoder_canceled", progress=progress) from None
                if isinstance(error, ModelRequestError):
                    raise
                if isinstance(error, TimeoutError):
                    raise failure("provider_timeout", "qoder_request_timeout", progress=progress) from None
                if isinstance(error, Exception):
                    raise failure("protocol_error", "qoder_worker_channel_failed", progress=progress) from None
                raise

    async def _watch_cancel(self, cancel: threading.Event, scope: anyio.CancelScope) -> None:
        while not cancel.is_set() and not self._closed:
            await anyio.sleep(0.025)
        scope.cancel()

    async def _stop(self) -> None:
        async with self._stop_lock:
            await self._stop_owned()

    async def _stop_owned(self) -> None:
        process = self._process
        if process is None:
            return
        # SDK ProcessTransport inherits this group. Give the worker a short
        # disconnect window, then terminate descendants as well as the owner.
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.send_signal(signal.SIGTERM)
            with anyio.move_on_after(2):
                await process.wait()
        for signum, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 1.0)):
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                break
            except PermissionError:
                # macOS can reject a signal after the final group member exits.
                # Only an absent group counts as quiescent; EPERM alone does not.
                pass
            with anyio.move_on_after(grace):
                while self._group_alive(process.pid):
                    await anyio.sleep(0.025)
            if not self._group_alive(process.pid):
                break
        if self._group_alive(process.pid):
            # Retain ownership so close/shutdown can retry and report failure.
            raise failure("protocol_error", "qoder_worker_cleanup_failed")
        await process.wait()
        await process.aclose()
        self._process = None
        self._output = None

    @staticmethod
    def _group_alive(pid: int) -> bool:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def close(self) -> None:
        self._closed = True
        with anyio.CancelScope(shield=True):
            await self._stop()
