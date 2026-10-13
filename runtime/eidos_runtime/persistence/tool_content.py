from __future__ import annotations

from eidos_runtime.db.database import Database
from eidos_runtime.db.json_blobs import JsonBlobReference, JsonBlobCorruptionError
from eidos_runtime.domain.tool_content import ToolContentSnapshot


TOOL_CONTENT_BLOB_KIND = "tool-result-content"


class ToolContentRepository:
    """Read committed tool observations without consulting their source files."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def read(self, stored: str) -> ToolContentSnapshot:
        return ToolContentSnapshot.model_validate_json(
            self.database.json_blobs.read_json(stored, expected_kind=TOOL_CONTENT_BLOB_KIND)
        )

    @staticmethod
    def stored_size(stored: str) -> int:
        reference = JsonBlobReference.from_json(stored)
        if reference is None or reference.kind != TOOL_CONTENT_BLOB_KIND:
            raise JsonBlobCorruptionError("tool content reference is invalid")
        return reference.uncompressed_bytes
