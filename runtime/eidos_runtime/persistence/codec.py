from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from eidos_runtime.db.database import now_ms
from pydantic import TypeAdapter, ValidationError

from eidos_runtime.persistence.conversion import (
    utc_datetime_from_millis as _utc_datetime_from_millis,
    utc_datetime_to_millis as _utc_datetime_to_millis,
)


_T = TypeVar("_T")
_STRING_TUPLE = TypeAdapter(tuple[str, ...])


def now_utc_millis() -> int:
    return now_ms()


def utc_datetime_from_millis(value: object) -> datetime:
    return _utc_datetime_from_millis(
        value,
        record="worktree-persistence",
        field="timestamp",
    )


def utc_datetime_to_millis(value: datetime) -> int:
    return _utc_datetime_to_millis(value)


def encode_string_tuple(value: tuple[str, ...]) -> str:
    return _STRING_TUPLE.dump_json(value).decode("utf-8")


def decode_string_tuple(value: object) -> tuple[str, ...]:
    if type(value) is not str:
        raise ValueError("strict JSON text is required")
    try:
        return _STRING_TUPLE.validate_json(value)
    except (TypeError, ValueError, ValidationError) as error:
        raise ValueError("strict string tuple JSON is invalid") from error


__all__ = [
    "decode_string_tuple",
    "encode_string_tuple",
    "now_utc_millis",
    "utc_datetime_from_millis",
    "utc_datetime_to_millis",
]
