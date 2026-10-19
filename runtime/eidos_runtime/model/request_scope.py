"""Foreground request ownership, propagated through the existing async portal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import time
from typing import ParamSpec, TypeVar

import anyio

from eidos_runtime.model.client import ModelRequestError, ModelRequestFailure

P = ParamSpec("P")
T = TypeVar("T")
_foreground = ContextVar("eidos_foreground_model_request", default=False)
_deadline: ContextVar[int | None] = ContextVar(
    "eidos_model_retry_deadline", default=None
)


def foreground_request_active() -> bool:
    return _foreground.get()


@contextmanager
def foreground_request(deadline_at: int | None) -> Iterator[None]:
    owner = _foreground.set(True)
    deadline = _deadline.set(deadline_at)
    try:
        yield
    finally:
        _deadline.reset(deadline)
        _foreground.reset(owner)


def check_request_deadline() -> None:
    deadline = _deadline.get()
    if deadline is not None and time.time_ns() // 1_000_000 >= deadline:
        raise _deadline_error()


def _deadline_error() -> ModelRequestError:
    return ModelRequestError(
        ModelRequestFailure(
            code="provider_timeout",
            reason="retry_window_exhausted",
            retryable=True,
        )
    )


async def run_model_request(
    function: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    deadline = _deadline.get()
    if deadline is None:
        return await function(*args, **kwargs)
    try:
        # CancelScope closes the previous request before control returns to Engine.
        with anyio.fail_after(
            max(0, (deadline - time.time_ns() // 1_000_000) / 1000)
        ) as scope:
            return await function(*args, **kwargs)
    except TimeoutError:
        if scope.cancel_called:
            raise _deadline_error() from None
        raise ModelRequestError(
            ModelRequestFailure(code="provider_timeout", retryable=True)
        ) from None
