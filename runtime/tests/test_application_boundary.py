from __future__ import annotations

from pathlib import Path

from eidos_runtime.application.runs import RunApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.database import Database
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.domain.run import Run
from eidos_runtime.domain.session import Session


def test_session_application_returns_domain_records_without_wire_dictionaries(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    database = Database(data)
    database.initialize()
    try:
        application = SessionApplication(SessionRepository(database))
        created = application.create(str(workspace))
        page = application.list()

        assert isinstance(created, Session)
        assert page.items == (created,)
        assert application.read(created.id) == created
    finally:
        database.close()


def test_run_application_reads_the_typed_repository_seam(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    database = Database(data)
    database.initialize()
    try:
        database.connection().execute(
            "INSERT INTO sessions (id, workspace_root, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("session-1", str(workspace), 1000, 1000),
        )
        database.connection().execute(
            """INSERT INTO runs (id, session_id, user_input, model_id, model_profile_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("run-1", "session-1", "goal", "model-1", "{}", "running", 1000, 1000),
        )
        database.connection().commit()

        application = RunApplication(database)
        assert isinstance(application.read("run-1"), Run)
        assert application.read("missing") is None
    finally:
        database.close()
