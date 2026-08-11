from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class DiffScope(StrEnum):
    HEAD = "head"
    BASELINE = "baseline"


class GitStatusSnapshot(EidosFrozenStrictModel):
    worktree_id: str
    repository_root: str
    worktree_root: str
    base_ref: str
    base_commit: str
    branch: str
    head: str
    dirty: bool
    staged_count: int = Field(ge=0)
    unstaged_count: int = Field(ge=0)
    untracked_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    observed_at: datetime


class GitDiffSnapshot(EidosFrozenStrictModel):
    scope: DiffScope
    base_commit: str
    head: str
    dirty: bool
    changed_files: tuple[str, ...]
    unified_diff: str
    truncated: bool
    observed_at: datetime


def parse_porcelain_v2_status(output: str) -> tuple[int, int, int, int]:
    staged = 0
    unstaged = 0
    untracked = 0
    conflicts = 0
    if "\x00" in output:
        if output and not output.endswith("\x00"):
            raise ValueError("Git status output is incomplete")
        records = output.split("\x00")[:-1]
    else:
        records = output.splitlines()
    index = 0
    while index < len(records):
        line = records[index]
        if not line:
            raise ValueError("Git status record is empty")
        if line.startswith("# "):
            if not line[2:]:
                raise ValueError("Git status header is malformed")
            index += 1
            continue
        if line.startswith("? "):
            if not line[2:]:
                raise ValueError("Git untracked record is malformed")
            untracked += 1
            index += 1
            continue
        if line.startswith("! "):
            if not line[2:]:
                raise ValueError("Git ignored record is malformed")
            index += 1
            continue
        if line.startswith("1 "):
            fields = _status_fields(line, maxsplit=8, expected=9)
            _validate_xy(fields[1])
        elif line.startswith("2 "):
            fields = _status_fields(line, maxsplit=9, expected=10)
            _validate_xy(fields[1])
            score = fields[8]
            if (
                len(score) < 2
                or score[0] not in {"R", "C"}
                or not score[1:].isdigit()
            ):
                raise ValueError("Git rename record is malformed")
            if "\x00" in output:
                index += 1
                if index >= len(records) or not records[index]:
                    raise ValueError("Git rename record is incomplete")
            elif "\t" not in fields[9]:
                raise ValueError("Git rename record is incomplete")
        elif line.startswith("u "):
            fields = _status_fields(line, maxsplit=10, expected=11)
            _validate_xy(fields[1])
            conflicts += 1
        else:
            raise ValueError("Git status record type is unknown")
        index_state, worktree_state = fields[1][0], fields[1][1]
        staged += int(index_state != ".")
        unstaged += int(worktree_state != ".")
        index += 1
    return staged, unstaged, untracked, conflicts


def _status_fields(line: str, *, maxsplit: int, expected: int) -> list[str]:
    fields = line.split(" ", maxsplit)
    if len(fields) != expected or any(not field for field in fields[1:]):
        raise ValueError("Git status record is malformed")
    if len(fields[2]) != 4:
        raise ValueError("Git status submodule field is malformed")
    return fields


def _validate_xy(value: str) -> None:
    if len(value) != 2 or any(character.isspace() for character in value):
        raise ValueError("Git status XY field is malformed")


def utc_now() -> datetime:
    return datetime.fromtimestamp(
        int(datetime.now(UTC).timestamp() * 1000) / 1000,
        tz=UTC,
    )


__all__ = [
    "DiffScope",
    "GitDiffSnapshot",
    "GitStatusSnapshot",
    "parse_porcelain_v2_status",
    "utc_now",
]
