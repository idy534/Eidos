from __future__ import annotations

from pathlib import Path

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory import contracts as dto
from eidos_runtime.memory.jobs import MemoryJobs
from eidos_runtime.memory.repository import MemoryRejected


class MemoryApplication:
    def __init__(self, store: SessionStore, jobs: MemoryJobs | None) -> None:
        self.service = store.database.memory
        self.jobs = jobs

    def backup(self, request: dto.MemoryBackupRequest) -> dto.MemoryBackupResult:
        from eidos_runtime.memory.backup import backup

        backup(self.service, Path(request.destination))
        return dto.MemoryBackupResult()

    def dispatch(self, method: str, request: object) -> object:
        handlers = {
            "memory/list": self.service.read,
            "memory/get": self.service.get,
            "memory/settingsUpdate": self.service.settings,
            "memory/temporary": self.service.set_temporary,
            "memory/remember": self.service.record,
            "memory/manage": self.service.manage,
            "memory/export": self.service.export,
            "memory/backup": self.backup,
            "memory/rebuild": self.service.rebuild,
        }
        if self.jobs is not None:
            handlers.update(
                {
                    "memory/backfill": self.jobs.backfill,
                    "memory/jobsRetry": self.jobs.retry,
                }
            )
        try:
            handler = handlers.get(method)
            if handler is None:
                raise MemoryRejected("memory_service_unavailable")
            return handler(request)
        except MemoryRejected as error:
            raise ApplicationError(str(error).upper()) from None
        except (OSError, ValueError):
            raise ApplicationError("MEMORY_IO_FAILED") from None


MEMORY_METHODS = (
    ("memory/list", dto.MemoryReadRequest, dto.MemoryState),
    ("memory/get", dto.MemoryGetRequest, dto.MemoryGetResult),
    ("memory/settingsUpdate", dto.MemorySettingsRequest, dto.MemoryState),
    ("memory/temporary", dto.MemoryTemporaryRequest, dto.MemoryState),
    ("memory/remember", dto.MemoryWriteRequest, dto.MemoryActionResult),
    ("memory/manage", dto.MemoryManageRequest, dto.MemoryActionResult),
    ("memory/export", dto.MemoryExportRequest, dto.MemoryExport),
    ("memory/backup", dto.MemoryBackupRequest, dto.MemoryBackupResult),
    ("memory/rebuild", dto.MemoryRebuildRequest, dto.MemoryState),
    ("memory/backfill", dto.MemoryBackfillRequest, dto.MemoryState),
    ("memory/jobsRetry", dto.MemoryJobRequest, dto.MemoryState),
)
