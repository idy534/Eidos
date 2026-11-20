"""Lossless SQLite storage for native filesystem identity numbers."""

from eidos_runtime.db.errors import StorageError


_SQLITE_INTEGER_MAX = (1 << 63) - 1
_FILE_ID_MAX = (1 << 128) - 1


def encode_file_identity(value: int) -> int | str:
    if not 0 <= value <= _FILE_ID_MAX:
        raise StorageError("filesystem identity number is invalid")
    if value <= _SQLITE_INTEGER_MAX:
        return value
    # The prefix prevents SQLite's INTEGER affinity from producing a REAL.
    return f"0x{value:x}"


def decode_file_identity(value: object) -> int:
    if type(value) is int and 0 <= value <= _SQLITE_INTEGER_MAX:
        return value
    if isinstance(value, str) and value.startswith("0x") and len(value) <= 34:
        try:
            number = int(value, 16)
        except ValueError:
            pass
        else:
            if _SQLITE_INTEGER_MAX < number <= _FILE_ID_MAX and encode_file_identity(number) == value:
                return number
    raise StorageError("filesystem identity number is invalid")
