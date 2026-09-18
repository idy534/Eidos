from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.protocol.registry import DeferredMethodResult
from eidos_runtime.protocol.server import _DeferredGitObserveAdapter


class _FakeResult:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_json_value(self) -> dict[str, object]:
        return dict(self._payload)


class _FakeSupervisor:
    def __init__(self, *, schedulable: bool = True) -> None:
        self.schedulable = schedulable
        self.targets: list[object] = []

    def start_managed_task(self, kind, target, **_kwargs):  # type: ignore[no-untyped-def]
        self.targets.append((kind, target))
        if not self.schedulable:
            return False
        thread = threading.Thread(
            target=target, args=(threading.Event(),), daemon=True
        )
        thread.start()
        return True


class _FakeServer:
    def __init__(self, *, method, schedulable: bool = True) -> None:
        self.sent: list[dict[str, object]] = []
        self._lock = threading.Lock()
        self.supervisor = _FakeSupervisor(schedulable=schedulable)
        self._applications_or_error = lambda: SimpleNamespace(
            sessions=SimpleNamespace(git_diff=method)
        )

    def send(self, message: dict[str, object]) -> None:
        with self._lock:
            self.sent.append(message)


def _wait_for_send(server: _FakeServer, count: int = 1) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with server._lock:
            if len(server.sent) >= count:
                return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for deferred observe response")


def test_slow_observe_does_not_block_input_loop() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_method(request):  # type: ignore[no-untyped-def]
        started.set()
        assert release.wait(timeout=5.0)
        return _FakeResult({"ok": True})

    server = _FakeServer(method=slow_method)
    adapter = _DeferredGitObserveAdapter(
        server, kind="session/gitDiff", method_name="git_diff"  # type: ignore[arg-type]
    )

    first = adapter("client-1", SimpleNamespace())
    assert isinstance(first, DeferredMethodResult)
    assert started.wait(timeout=5.0)

    # The input loop must accept the next request while the first is in flight.
    second = adapter("client-2", SimpleNamespace())
    assert isinstance(second, DeferredMethodResult)

    release.set()
    _wait_for_send(server, count=2)
    ids = {message["id"] for message in server.sent}
    assert ids == {"client-1", "client-2"}


def test_observe_application_error_maps_to_business_error() -> None:
    def failing_method(request):  # type: ignore[no-untyped-def]
        raise ApplicationError("GIT_COMMAND_TIMEOUT")

    server = _FakeServer(method=failing_method)
    adapter = _DeferredGitObserveAdapter(
        server, kind="session/gitDiff", method_name="git_diff"  # type: ignore[arg-type]
    )
    assert isinstance(adapter("client-9", SimpleNamespace()), DeferredMethodResult)
    _wait_for_send(server, count=1)
    message = server.sent[0]
    assert message["id"] == "client-9"
    assert message["error"]["data"]["code"] == "GIT_COMMAND_TIMEOUT"  # type: ignore[index]


def test_draining_falls_back_to_synchronous_result() -> None:
    server = _FakeServer(
        method=lambda request: _FakeResult({"ok": True}), schedulable=False
    )
    adapter = _DeferredGitObserveAdapter(
        server, kind="session/gitDiff", method_name="git_diff"  # type: ignore[arg-type]
    )
    result = adapter("client-7", SimpleNamespace())
    assert isinstance(result, _FakeResult)
    assert server.sent == []
