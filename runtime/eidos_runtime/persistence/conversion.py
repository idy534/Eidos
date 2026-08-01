from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import math
import sqlite3
from types import TracebackType
from typing import Iterator, Protocol, runtime_checkable

from pydantic import JsonValue

from eidos_runtime.models import JSON_SAFE_INTEGER_MAX
from eidos_runtime.persistence.errors import PersistenceCorruptionError


SQLITE_INTEGER_MIN = -(2**63)
SQLITE_INTEGER_MAX = 2**63 - 1
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class RowValues(Protocol):
    def keys(self) -> Iterable[str]: ...

    def __getitem__(self, key: str) -> object: ...


class TransactionLock(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class ReadTransaction(Protocol):
    def execute(
        self, sql: str, parameters: Iterable[object] = (), /
    ) -> sqlite3.Cursor: ...


@runtime_checkable
class WriteTransaction(ReadTransaction, Protocol):
    def executemany(
        self, sql: str, parameters: Iterable[Iterable[object]], /
    ) -> sqlite3.Cursor: ...

    def __enter__(self) -> WriteTransaction: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


@contextmanager
def read_transaction(
    lock: TransactionLock, connection: ReadTransaction
) -> Iterator[ReadTransaction]:
    with lock:
        yield connection


@contextmanager
def write_transaction(
    lock: TransactionLock, connection: WriteTransaction
) -> Iterator[WriteTransaction]:
    with lock, connection as transaction:
        yield transaction


class RowReader:
    def __init__(self, row: RowValues | Mapping[str, object], *, record: str) -> None:
        self._row = row
        self.record = record
        self._columns = frozenset(row.keys())

    def value(self, field: str) -> object:
        if field not in self._columns:
            raise PersistenceCorruptionError(
                "persistence_column_missing",
                record=self.record,
                field=field,
            )
        return self._row[field]

    def text(self, field: str) -> str:
        value = self.value(field)
        if type(value) is not str:
            self._invalid(field)
        return value

    def optional_text(self, field: str) -> str | None:
        value = self.value(field)
        if value is None:
            return None
        if type(value) is not str:
            self._invalid(field)
        return value

    def integer(self, field: str) -> int:
        return sqlite_safe_integer(
            self.value(field), record=self.record, field=field
        )

    def optional_integer(self, field: str) -> int | None:
        value = self.value(field)
        if value is None:
            return None
        return sqlite_safe_integer(value, record=self.record, field=field)

    def boolean(self, field: str) -> bool:
        value = sqlite_safe_integer(
            self.value(field), record=self.record, field=field
        )
        if value not in (0, 1):
            self._invalid(field)
        return bool(value)

    def optional_boolean(self, field: str) -> bool | None:
        value = self.value(field)
        if value is None:
            return None
        return self.boolean(field)

    def real(self, field: str) -> float:
        value = self.value(field)
        if type(value) not in (int, float) or not math.isfinite(value):
            self._invalid(field)
        return float(value)

    def optional_real(self, field: str) -> float | None:
        value = self.value(field)
        if value is None:
            return None
        return self.real(field)

    def json_text(self, field: str) -> str:
        value = self.text(field)
        try:
            json.loads(value, parse_constant=lambda _value: _invalid_json())
        except (TypeError, ValueError, json.JSONDecodeError):
            _raise_json_invalid(self.record, field)
        return value

    def optional_json_text(self, field: str) -> str | None:
        value = self.value(field)
        if value is None:
            return None
        return self.json_text(field)

    def _invalid(self, field: str) -> None:
        raise PersistenceCorruptionError(
            "persistence_value_invalid",
            record=self.record,
            field=field,
        )


def sqlite_safe_integer(value: object, *, record: str, field: str) -> int:
    if (
        type(value) is not int
        or value < SQLITE_INTEGER_MIN
        or value > SQLITE_INTEGER_MAX
    ):
        raise PersistenceCorruptionError(
            "persistence_value_invalid",
            record=record,
            field=field,
        )
    return value


def utc_datetime_from_millis(
    value: object, *, record: str, field: str
) -> datetime:
    milliseconds = sqlite_safe_integer(value, record=record, field=field)
    if milliseconds < 0:
        raise PersistenceCorruptionError(
            "persistence_value_invalid",
            record=record,
            field=field,
        )
    seconds, remainder = divmod(milliseconds, 1_000)
    try:
        return _UNIX_EPOCH + timedelta(
            seconds=seconds, milliseconds=remainder
        )
    except OverflowError:
        raise PersistenceCorruptionError(
            "persistence_value_invalid",
            record=record,
            field=field,
        ) from None


def utc_datetime_to_millis(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be UTC")
    normalized = value.astimezone(UTC)
    if normalized.microsecond % 1_000:
        raise ValueError("timestamp must use UTC millisecond precision")
    delta = normalized - _UNIX_EPOCH
    milliseconds = (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )
    if not SQLITE_INTEGER_MIN <= milliseconds <= SQLITE_INTEGER_MAX:
        raise ValueError("timestamp exceeds SQLite integer range")
    return milliseconds


def decode_json_object(
    value: object, *, record: str, field: str
) -> dict[str, JsonValue]:
    if type(value) is not str:
        _raise_json_invalid(record, field)
    try:
        decoded = json.loads(value, parse_constant=lambda _value: _invalid_json())
    except (TypeError, ValueError, json.JSONDecodeError):
        _raise_json_invalid(record, field)
    if not isinstance(decoded, dict):
        _raise_json_invalid(record, field)
    try:
        _validate_json_value(decoded)
    except ValueError:
        _raise_json_invalid(record, field)
    return decoded


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            raise ValueError
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError
            _validate_json_value(item)
        return
    raise ValueError


def _invalid_json() -> None:
    raise ValueError


def _raise_json_invalid(record: str, field: str) -> None:
    raise PersistenceCorruptionError(
        "persistence_json_invalid",
        record=record,
        field=field,
    ) from None
