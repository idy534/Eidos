from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

import pytest

from eidos_runtime.db.layout import AuxiliaryDatabase, LOGS_DATABASE_NAME, LOGS_SCHEMA_SQL
from eidos_runtime.db.runtime_logs import RuntimeLogStore


def _write_record(tmp_path: Path, message: str, error: str | None = None) -> dict:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = AuxiliaryDatabase(data, name=LOGS_DATABASE_NAME, schema_sql=LOGS_SCHEMA_SQL)
    database.initialize()
    try:
        info = None
        if error is not None:
            try:
                try:
                    raise OSError("upstream failed api_key=hidden-cause-key")
                except OSError as cause:
                    raise ValueError(error) from cause
            except ValueError:
                info = sys.exc_info()
        logger = logging.getLogger("eidos.test.diagnostics")
        RuntimeLogStore(data, database).append(logger.makeRecord(
            logger.name, logging.ERROR if info else logging.INFO,
            __file__, 1, message, (), info,
        ))
        paths = list((data / "logs").rglob("*.jsonl"))
        assert len(paths) == 1
        return json.loads(paths[0].read_text())
    finally:
        database.close()


def test_runtime_log_preserves_bounded_exception_cause_and_locations(tmp_path: Path) -> None:
    record = _write_record(tmp_path, "Run worker failed", "json blob exceeds size limit")
    assert record["exceptionType"] == "ValueError"
    assert record["exceptionMessage"] == "json blob exceeds size limit"
    stack = record["exceptionTraceback"]
    assert "ValueError: json blob exceeds size limit" in stack
    assert "OSError: upstream failed" in stack
    assert "test_runtime_log_diagnostics.py" in stack
    assert "in _write_record" in stack
    # Source code and locals must never be included in the stack.
    assert "raise ValueError(error)" not in stack
    assert "hidden-cause-key" not in json.dumps(record)


@pytest.mark.parametrize("secret", [
    "api_key=hidden-unit-key",
    'api-key="hidden-unit-key"',
    "Authorization: Bearer hidden-unit-key",
    'password="hidden-unit-key with spaces"',
    "sk-abcdefghijklmnopq",
    "-----BEGIN PRIVATE KEY-----\nhidden-unit-key\n-----END PRIVATE KEY-----",
])
def test_runtime_logs_redact_messages_and_exception_details(tmp_path: Path, secret: str) -> None:
    record = _write_record(tmp_path, f"failure {secret}", f"upstream {secret}")
    raw = json.dumps(record)
    assert "hidden-unit-key" not in raw
    assert "sk-abcdefghijklmnopq" not in raw
    assert "[REDACTED" in raw
    assert record["exceptionType"] == "ValueError"


def test_runtime_exception_and_log_messages_have_utf8_byte_bounds(tmp_path: Path) -> None:
    record = _write_record(tmp_path, "界" * 30000, "界" * 30000)
    assert len(record["message"].encode()) <= 16 * 1024
    assert len(record["exceptionMessage"].encode()) <= 4 * 1024
    assert len(record["exceptionTraceback"].encode()) <= 16 * 1024


def test_normal_log_records_do_not_gain_exception_fields(tmp_path: Path) -> None:
    record = _write_record(tmp_path, "Runtime ready")
    assert record["message"] == "Runtime ready"
    assert record["exceptionType"] is None
    assert "exceptionMessage" not in record
    assert "exceptionTraceback" not in record


def test_redaction_failure_keeps_a_safe_diagnostic_record(tmp_path: Path, monkeypatch) -> None:
    def unavailable():
        raise RuntimeError("redactor unavailable secret=do-not-persist")

    monkeypatch.setattr("eidos_runtime.db.runtime_logs.default_scanner", unavailable)
    record = _write_record(tmp_path, "worker failed", "secret=do-not-persist")
    assert record["exceptionType"] == "ValueError"
    assert "do-not-persist" not in json.dumps(record)
    assert record["message"] == "[REDACTED:diagnostic_unavailable]"
    assert record["exceptionTraceback"] == "[REDACTED:diagnostic_unavailable]"
