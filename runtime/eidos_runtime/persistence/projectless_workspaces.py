"""SQLite reservations for new Projectless workspaces, never legacy relocation."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
from typing import Literal
import uuid
from pydantic import Field

from eidos_runtime.db.database import Repository, WorkspaceIdentity, canonical_hash, now_ms
from eidos_runtime.db.errors import OperationConflictError, StorageError, WorkspaceBoundaryError
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.workspace.projectless import MAX_NEW_NAME_BYTES, creation_date, readable_workspace_name


class ProjectlessWorkspaceCreation(EidosFrozenStrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_id: str = Field(min_length=1, max_length=128)
    base_name: str = Field(min_length=1, max_length=96)
    suffix: JsonSafeInt = Field(ge=1)
    workspace_root: str = Field(min_length=1, max_length=4096)
    status: Literal["prepared", "created", "completed"]
    workspace_dev: JsonSafeInt | None
    workspace_inode: JsonSafeInt | None
    workspace_uid: JsonSafeInt | None
    created_at: JsonSafeInt = Field(ge=0)

    def identity(self) -> WorkspaceIdentity:
        if any(value is None for value in (
            self.workspace_dev, self.workspace_inode, self.workspace_uid
        )):
            raise StorageError("projectless_workspace_identity_unavailable")
        return WorkspaceIdentity(
            path=Path(self.workspace_root), device=self.workspace_dev,
            inode=self.workspace_inode, owner=self.workspace_uid,
        )


class ProjectlessWorkspaceRepository(Repository):
    def prepare(self, operation_id: str, name: str, root: Path) -> ProjectlessWorkspaceCreation:
        request_hash = canonical_hash({"workspaceName": name})
        if self._workspace_overlaps_data(root):
            raise WorkspaceBoundaryError("workspace overlaps runtime data")
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM projectless_workspace_creations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise OperationConflictError("operation id was reused")
                if Path(existing["workspace_root"]).parent.parent != root:
                    raise WorkspaceBoundaryError("projectless workspace is outside its managed root")
                return _creation(existing)
            if connection.execute(
                "SELECT 1 FROM operations WHERE id = ? AND scope = 'session/create'", (operation_id,),
            ).fetchone() is not None:
                raise OperationConflictError("operation id was reused")
            # Keep the original request identity and existing reservations across upgrades.
            name = readable_workspace_name(name, max_bytes=MAX_NEW_NAME_BYTES)
            timestamp = now_ms()
            parent = root / creation_date(timestamp)
            suffix, path = self._available_path(connection, parent, name, 1)
            connection.execute(
                """INSERT INTO projectless_workspace_creations (
                    operation_id, request_hash, session_id, base_name, suffix,
                    workspace_root, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?)""",
                (operation_id, request_hash, str(uuid.uuid4()), name, suffix, str(path), timestamp),
            )
            return self._read(connection, operation_id)

    def assert_project_operation(self, operation_id: str) -> None:
        with self.lock:
            if self._connection().execute(
                "SELECT 1 FROM projectless_workspace_creations WHERE operation_id = ?", (operation_id,),
            ).fetchone() is not None:
                raise OperationConflictError("operation id was reused")

    def next_path(self, creation: ProjectlessWorkspaceCreation) -> ProjectlessWorkspaceCreation:
        with self.database.transaction() as connection:
            current = self._read(connection, creation.operation_id)
            if current != creation or current.status != "prepared":
                raise StorageError("projectless_workspace_creation_changed")
            suffix, path = self._available_path(
                connection, Path(current.workspace_root).parent,
                current.base_name, current.suffix + 1,
            )
            connection.execute(
                """UPDATE projectless_workspace_creations SET suffix = ?, workspace_root = ?
                   WHERE operation_id = ?""",
                (suffix, str(path), current.operation_id),
            )
            return self._read(connection, current.operation_id)

    def mark_created(
        self, creation: ProjectlessWorkspaceCreation, identity: WorkspaceIdentity,
    ) -> ProjectlessWorkspaceCreation:
        with self.database.transaction() as connection:
            current = self._read(connection, creation.operation_id)
            if current != creation or str(identity.path) != current.workspace_root:
                raise StorageError("projectless_workspace_creation_changed")
            connection.execute(
                """UPDATE projectless_workspace_creations
                   SET status = 'created', workspace_dev = ?, workspace_inode = ?, workspace_uid = ?
                   WHERE operation_id = ? AND status = 'prepared'""",
                (identity.device, identity.inode, identity.owner, current.operation_id),
            )
            return self._read(connection, current.operation_id)

    def abandon(self, creation: ProjectlessWorkspaceCreation) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM projectless_workspace_creations WHERE operation_id = ? AND status != 'completed'",
                (creation.operation_id,),
            )

    @staticmethod
    def _available_path(
        connection: sqlite3.Connection, parent: Path, name: str, suffix: int,
    ) -> tuple[int, Path]:
        for number in range(suffix, suffix + 1000):
            path = parent / (name if number == 1 else f"{name}-{number}")
            if connection.execute(
                "SELECT 1 FROM projectless_workspace_creations WHERE workspace_root = ?",
                (str(path),),
            ).fetchone() is None:
                return number, path
        raise StorageError("projectless_workspace_name_exhausted")

    @staticmethod
    def _read(connection: sqlite3.Connection, operation_id: str) -> ProjectlessWorkspaceCreation:
        row = connection.execute(
            "SELECT * FROM projectless_workspace_creations WHERE operation_id = ?", (operation_id,),
        ).fetchone()
        if row is None:
            raise StorageError("projectless_workspace_creation_missing")
        return _creation(row)


def _creation(row: sqlite3.Row) -> ProjectlessWorkspaceCreation:
    try:
        creation = ProjectlessWorkspaceCreation.model_validate(dict(row))
        path = Path(creation.workspace_root)
        day = date.fromisoformat(path.parent.name)
        name = creation.base_name if creation.suffix == 1 else f"{creation.base_name}-{creation.suffix}"
        if (
            not path.is_absolute() or ".." in path.parts
            or str(path) != creation.workspace_root or day.isoformat() != path.parent.name
            or readable_workspace_name(creation.base_name) != creation.base_name or path.name != name
        ):
            raise ValueError("invalid workspace creation path")
        return creation
    except ValueError as error:
        raise StorageError("projectless_workspace_creation_invalid") from error
