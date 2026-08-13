from __future__ import annotations

from pathlib import Path

import pytest

from eidos_runtime.context.facts import CompactSummary
from eidos_runtime.context.compactor import ContextCompactor
from eidos_runtime.context.verified_compaction import CompactionVerificationError
from eidos_runtime.db.storage import SessionStore


def test_verified_compaction_persists_with_outbox_and_failure_keeps_previous(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, user_item = store.enqueue_run(session["id"], "finish the task")
    store.claim_next_run_committed()
    repository = store.verified_compaction_repository()
    summary = CompactSummary(
        task_goal="finish the task",
        constraints=("keep SQLite authoritative",),
        completed_actions=(),
        workspace_changes=(),
        important_facts=("task started",),
        unresolved_problems=(),
        next_actions=("continue",),
        source_item_ids=(user_item["id"],),
    )
    before_outbox = store.pending_outbox_count()

    verified = repository.verify_and_persist(
        run_id=run["id"], summary=summary, input_range=(1, 1)
    )

    assert repository.latest(run["id"]) == verified
    assert store.latest_compact_summary(run["id"]) == summary
    assert store.compaction_count(run["id"]) == 1
    assert store.pending_outbox_count() == before_outbox + 1
    duplicate = repository.verify_and_persist(
        run_id=run["id"], summary=summary, input_range=(1, 1)
    )
    assert duplicate.summary_hash == verified.summary_hash
    assert store.compaction_count(run["id"]) == 1
    assert store.pending_outbox_count() == before_outbox + 1
    invented = summary.model_copy(update={"workspace_changes": ("invented.py",)})
    with pytest.raises(CompactionVerificationError, match="workspace change"):
        repository.verify_and_persist(
            run_id=run["id"], summary=invented, input_range=(1, 1)
        )
    assert repository.latest(run["id"]) == verified
    assert store.latest_compact_summary(run["id"]) == summary
    assert store.compaction_count(run["id"]) == 1
    assert store.pending_outbox_count() == before_outbox + 1
    store.close()


def test_pre_turn_compaction_records_only_source_item_tool_provenance(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    previous, _item = store.create_run(session["id"], "inspect previous state")
    source_item = store.create_tool_item(
        previous["id"], 1, 0, "source-call", "read_file", "{}"
    )
    store.complete_tool_item(
        source_item["id"],
        '{"outcome":"success","data":{"path":"main.py"}}',
    )
    assert store.connection is not None
    source_tool_id = str(store.connection.execute(
        "SELECT id FROM tool_calls WHERE item_id = ?", (source_item["id"],)
    ).fetchone()[0])
    store.fail_run(previous["id"], "fixture")
    current, _item = store.create_run(session["id"], "continue")
    unrelated_item = store.create_tool_item(
        current["id"], 1, 0, "unrelated-call", "read_file", "{}"
    )
    store.complete_tool_item(
        unrelated_item["id"],
        '{"outcome":"success","data":{"path":"other.py"}}',
    )
    unrelated_tool_id = str(store.connection.execute(
        "SELECT id FROM tool_calls WHERE item_id = ?", (unrelated_item["id"],)
    ).fetchone()[0])

    ContextCompactor(store).compact(current["id"], "pre_turn")

    verified = store.verified_compaction_repository().latest(current["id"])
    assert verified is not None
    assert source_tool_id in verified.source_tool_call_ids
    assert unrelated_tool_id not in verified.source_tool_call_ids
    store.close()
