from __future__ import annotations

from pathlib import Path
import json

import pytest
from pydantic import ValidationError

from eidos_runtime.context.budget import estimate_context_budget
from eidos_runtime.context.plan import ContextPlanner, ContextSnapshot
from eidos_runtime.db.database import Database
from eidos_runtime.model.client import ModelProfileSnapshot, ModelToolDefinition
from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot


def _profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        provider_id="provider",
        model_id="model",
        context_window_tokens=4096,
        max_output_tokens=512,
        request_timeout_seconds=30.0,
        supports_tools=True,
        supports_json_schema_output=True,
        supports_reasoning=False,
    )


def test_context_plan_captures_canonical_builder_payload_without_reprojecting(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    rules = RuleResolutionSnapshot.create(
        workspace_root=str(root),
        cwd=str(root),
        budget_bytes=32 * 1024,
        used_bytes=0,
        rules=(),
        shadowed=(),
        warnings=(),
    )
    context = (
        {"type": "user", "sectionId": "workspace", "content": "goal"},
        {"type": "tool_call", "callId": "call-1", "name": "read_file", "arguments": "{}"},
        {"type": "tool_result", "callId": "call-1", "name": "read_file", "result": "{}"},
    )
    tools = (
        ModelToolDefinition(
            name="read_file", description="Read", parameters_json_schema={"type": "object"}
        ),
    )
    budget = estimate_context_budget(
        {"instructions": "rules", "messages": context},
        context_window_tokens=4096,
        request_max_output_tokens=512,
        message_count=len(context),
        tool_call_count=1,
        tool_result_count=1,
    )

    plan = ContextPlanner().capture(
        model_profile=_profile(),
        rule_snapshot=rules,
        model_context=context,
        instructions="rules",
        tool_definitions=tools,
        token_budget=budget,
    )
    snapshot = plan.for_model_attempt(
        "attempt-1",
        model_context=context,
        instructions="rules",
        tool_definitions=tools,
    )

    assert snapshot.model_context == context
    assert snapshot.instructions == "rules"
    assert snapshot.tool_definitions == tools
    assert plan.inventory_snapshot_id is None
    assert plan.retrieval_snapshot_id is None
    with pytest.raises(ValueError, match="does not match plan"):
        plan.for_model_attempt(
            "attempt-2",
            model_context=({"type": "user", "content": "changed"},),
            instructions="rules",
            tool_definitions=tools,
        )
    with pytest.raises(ValidationError):
        ContextSnapshot.model_validate({
            **snapshot.model_dump(mode="json"),
            "instructions": "tampered",
        })


def test_context_snapshot_without_repository_lineage_round_trips_sqlite(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    rules = RuleResolutionSnapshot.create(
        workspace_root=str(root), cwd=str(root), budget_bytes=1024,
        used_bytes=0, rules=(), shadowed=(), warnings=(),
    )
    context = ({"type": "user", "content": "goal"},)
    budget = estimate_context_budget(
        context,
        context_window_tokens=4096,
        request_max_output_tokens=512,
        message_count=1,
        tool_call_count=0,
        tool_result_count=0,
    )
    plan = ContextPlanner().capture(
        model_profile=_profile(),
        rule_snapshot=rules,
        model_context=context,
        instructions="",
        tool_definitions=(),
        token_budget=budget,
    )
    snapshot = plan.for_model_attempt(
        "attempt-1", model_context=context, instructions="", tool_definitions=()
    )
    database = Database(tmp_path / "data")
    database.initialize()
    try:
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO sessions (id, workspace_root, created_at, updated_at) "
                "VALUES ('session', ?, 1, 1)",
                (str(root),),
            )
            connection.execute(
                """
                INSERT INTO runs (
                    id, session_id, user_input, model_profile_json, status,
                    created_at, updated_at
                ) VALUES ('run', 'session', 'goal', '{}', 'running', 1, 1)
                """
            )
        repository = ContextSnapshotRepository(database)
        assert repository.persist(
            run_id="run", retrieval=None, snapshot=snapshot
        ) == snapshot
        assert repository.read_for_model_attempt("attempt-1") == snapshot
        stored = database.connection().execute(
            "SELECT snapshot_json FROM context_snapshots WHERE id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()[0]
        reference = json.loads(stored)["$eidosBlob"]
        assert reference["kind"] == "context-snapshot"
        assert reference["sha256"]
        assert len(stored) < 512
        assert (
            database.data_directory / "blobs" / reference["relativePath"]
        ).is_file()
    finally:
        database.close()

    reopened = Database(tmp_path / "data")
    reopened.initialize()
    try:
        repository = ContextSnapshotRepository(reopened)
        assert repository.read(snapshot.snapshot_id) == snapshot
        stored = reopened.connection().execute(
            "SELECT snapshot_json FROM context_snapshots WHERE id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()[0]
        reference = json.loads(stored)["$eidosBlob"]
        (
            reopened.data_directory / "blobs" / reference["relativePath"]
        ).unlink()
        with pytest.raises(
            PersistenceCorruptionError,
            match="persistence_record_invalid",
        ):
            repository.read(snapshot.snapshot_id)
    finally:
        reopened.close()
