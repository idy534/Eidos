from __future__ import annotations

import hashlib
import json

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.protocol.methods import SessionReadResponseDto


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _insert_declare_outputs(
    store: SessionStore,
    *,
    session_id: str,
    run_id: str,
    item_id: str,
    tool_id: str,
    ordinal: int,
    workspace: str,
    outputs: list[dict[str, object]],
    source_id: str = "eidos.declare-outputs",
    outcome: str = "success",
    item_status: str = "completed",
) -> None:
    now = 1_700_000_000_000 + ordinal
    provenance = json.dumps({
        "kind": "builtin",
        "sourceId": source_id,
        "sourceVersion": "1",
        "contentHash": _hash("schema"),
    })
    result = json.dumps({
        "outcome": outcome,
        "code": "ok" if outcome == "success" else "error",
        "summary": "declared",
        "data": {"executionRoot": workspace, "outputs": outputs},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    })
    with store.connection:
        store.connection.execute(
            """
            INSERT INTO items (id, session_id, run_id, ordinal, kind, status, created_at, completed_at)
            VALUES (?, ?, ?, ?, 'tool_call', ?, ?, ?)
            """,
            (item_id, session_id, run_id, ordinal, item_status, now, now),
        )
        store.connection.execute(
            """
            INSERT INTO tool_calls (
                id, item_id, model_step_index, batch_order, provider_call_id,
                tool_name, status, payload_kind, arguments_json, result_json,
                ui_result_json, provenance_json, started_at, completed_at
            ) VALUES (?, ?, 1, 0, ?, 'declare_outputs', 'completed', 'function', '{}', ?, ?, ?, ?, ?)
            """,
            (tool_id, item_id, f"call-{tool_id}", result, result, provenance, now, now),
        )


def test_session_read_returns_declared_output_summaries_across_older_pages(tmp_path) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        workspace = (tmp_path / "workspace").resolve()
        workspace.mkdir()
        session = store.create_session(str(workspace))
        session_id = str(session["id"])
        run1, _ = store.create_run(session_id, "first turn")

        # Put valid declare_outputs early, malformed/non-builtin ones that should be ignored,
        # an updated declaration for report.pdf, and then push extra items so item_limit=2
        # pages out the declare_outputs items.
        _insert_declare_outputs(
            store,
            session_id=session_id,
            run_id=str(run1["id"]),
            item_id="item-decl-1",
            tool_id="tool-decl-1",
            ordinal=2,
            workspace=str(workspace),
            outputs=[
                {"path": "report.pdf", "sizeBytes": 120, "version": "a" * 64, "title": "Draft"},
                {"path": "chart.png", "sizeBytes": 0, "version": "b" * 64},
            ],
        )
        _insert_declare_outputs(
            store,
            session_id=session_id,
            run_id=str(run1["id"]),
            item_id="item-decl-wrong-source",
            tool_id="tool-decl-wrong-source",
            ordinal=3,
            workspace=str(workspace),
            source_id="other.tool",
            outputs=[{"path": "ignored.pdf", "sizeBytes": 10, "version": "c" * 64}],
        )
        _insert_declare_outputs(
            store,
            session_id=session_id,
            run_id=str(run1["id"]),
            item_id="item-decl-bad-batch",
            tool_id="tool-decl-bad-batch",
            ordinal=4,
            workspace=str(workspace),
            outputs=[
                {"path": "report.pdf", "sizeBytes": 999, "version": "d" * 64, "title": "Poison"},
                {"path": "broken.txt", "sizeBytes": 1, "version": "not-a-sha"},
            ],
        )
        _insert_declare_outputs(
            store,
            session_id=session_id,
            run_id=str(run1["id"]),
            item_id="item-decl-2",
            tool_id="tool-decl-2",
            ordinal=5,
            workspace=str(workspace),
            outputs=[
                {"path": "report.pdf", "sizeBytes": 240, "version": "e" * 64, "title": "Final"},
            ],
        )
        with store.connection:
            for offset in range(6, 10):
                store.connection.execute(
                    """
                    INSERT INTO items (id, session_id, run_id, ordinal, kind, status, content, created_at, completed_at)
                    VALUES (?, ?, ?, ?, 'assistant_message', 'completed', 'later message', ?, ?)
                    """,
                    (f"item-later-{offset}", session_id, str(run1["id"]), offset, 1_700_000_000_000 + offset, 1_700_000_000_000 + offset),
                )

        latest_snapshot = store.read_session_snapshot(session_id, item_limit=2)
        parsed = SessionReadResponseDto.model_validate(latest_snapshot)
        assert parsed.previous_item_id is not None
        assert parsed.declared_outputs is not None
        assert [(d.path, d.title, d.size_bytes, d.item_id) for d in parsed.declared_outputs] == [
            ("report.pdf", "Final", 240, "item-decl-2"),
            ("chart.png", None, 0, "item-decl-1"),
        ]
        wire = parsed.to_json_value()
        assert wire["declaredOutputs"][1]["sizeBytes"] == 0

        # Pagination fetches (beforeItemId != None) omit redundant full declaredOutputs summary.
        older_snapshot = store.read_session_snapshot(
            session_id, item_limit=2, before_item_id=parsed.previous_item_id
        )
        older_parsed = SessionReadResponseDto.model_validate(older_snapshot)
        assert older_parsed.declared_outputs is None
        assert "declaredOutputs" not in older_parsed.to_json_value()
    finally:
        store.close()
