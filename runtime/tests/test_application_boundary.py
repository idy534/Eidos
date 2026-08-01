from __future__ import annotations

from pathlib import Path

from eidos_runtime.application.context import ContextApplication
from eidos_runtime.application.repository import RepositoryApplication
from eidos_runtime.application.runs import RunApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.database import Database
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.domain.run import Run
from eidos_runtime.domain.session import Session
from eidos_runtime.context.facts import CompactSummary, ContextFacts
from eidos_runtime.context.plan import ContextPlanner
from eidos_runtime.repo_intelligence.retrieval import RepositoryRetrievalQuery


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


def test_repository_application_returns_an_immutable_complete_analysis(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def main():\n    return 'ok'\n", encoding="utf-8")

    application = RepositoryApplication(root)
    snapshot = application.build()
    retrieval = application.retrieve(
        snapshot,
        RepositoryRetrievalQuery(text="main", mentioned_symbols=("main",)),
    )

    assert snapshot.complete is True
    assert snapshot.index is not None
    assert snapshot.repository_map is not None
    assert retrieval.results


def test_context_application_delegates_typed_plan_and_compaction_verification() -> None:
    application = ContextApplication(planner=ContextPlanner())
    summary = CompactSummary(
        task_goal="goal",
        constraints=(),
        completed_actions=(),
        workspace_changes=(),
        important_facts=(),
        unresolved_problems=(),
        next_actions=(),
        source_item_ids=("item-1",),
    )
    facts = ContextFacts(
        run_id="run-1",
        session_id="session-1",
        items=(
            {
                "item_id": "item-1",
                "run_id": "run-1",
                "kind": "user_message",
                "status": "completed",
            },
        ),
    )

    verified = application.verify_compaction(summary, facts, input_range=(0, 1))

    assert verified.verification_result == "verified"
