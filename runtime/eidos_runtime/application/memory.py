from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory import contracts as dto
from eidos_runtime.memory.repository import MemoryRejected


class MemoryApplication:
    def __init__(
        self, store: SessionStore, on_change: Callable[[], None] | None = None
    ) -> None:
        self.service = store.database.memory
        self.on_change = on_change

    def backup(self, request: dto.DataBackupRequest) -> dto.DataBackupResult:
        from eidos_runtime.db.backup import backup

        backup(self.service, Path(request.destination))
        return dto.DataBackupResult()

    def dispatch(self, method: str, request: object) -> object:
        handlers = {
            "memory/list": self.service.list,
            "memory/read": self.service.read,
            "memory/edit": self.service.edit,
            "memory/policy": self.service.policy,
            "memory/temporary": self.service.set_temporary,
            "memory/export": self.service.export,
            "data/backup": self.backup,
        }
        try:
            handler = handlers.get(method)
            if handler is None:
                raise MemoryRejected("memory_method_unavailable")
            result = handler(request)
            if self.on_change is not None and method in {
                "memory/edit",
                "memory/policy",
                "memory/temporary",
            }:
                self.on_change()
            return result
        except MemoryRejected as error:
            raise ApplicationError(str(error).upper()) from None
        except (OSError, ValueError):
            raise ApplicationError(
                "DATA_BACKUP_IO_FAILED"
                if method == "data/backup"
                else "MEMORY_IO_FAILED"
            ) from None


MEMORY_METHODS = (
    ("memory/list", dto.MemoryListRequest, dto.MemoryState),
    ("memory/read", dto.MemoryReadRequest, dto.MemoryReadResult),
    ("memory/edit", dto.MemoryEditRequest, dto.MemoryActionResult),
    ("memory/policy", dto.MemoryPolicyRequest, dto.MemoryState),
    ("memory/temporary", dto.MemoryTemporaryRequest, dto.MemoryState),
    ("memory/export", dto.MemoryExportRequest, dto.MemoryExport),
    ("data/backup", dto.DataBackupRequest, dto.DataBackupResult),
)
