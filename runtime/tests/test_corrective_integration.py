from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.runtime.engine import RuntimeEngine


def test_projection_recovery_preserves_scoped_discovery_and_deduplicates_reads() -> None:
    with tempfile.TemporaryDirectory(prefix="eidos-corrective-") as temporary:
        root = Path(temporary)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        store = SessionStore(data)
        store.initialize()
        try:
            session = store.create_session(str(workspace))
            old, _ = store.create_run(session["id"], "old history")
            assert store.connection is not None
            now = int(time.time() * 1000)
            store.connection.executemany(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, kind, status,
                    content, incomplete, created_at, completed_at
                ) VALUES (?, ?, ?, ?, 'assistant_message', 'completed', ?, 0, ?, ?)
                """,
                (
                    (
                        f"old-{index}", session["id"], old["id"], index + 2,
                        "x" * 5_000, now + index, now + index,
                    )
                    for index in range(250)
                ),
            )
            store.connection.commit()
            store.fail_run(old["id"], "fixture")
            current, _ = store.create_run(
                session["id"],
                "Analyze startup flow with scoped discovery.",
                model_profile=default_profile_snapshot("deepseek-v4-flash").model_copy(
                    update={"context_window_tokens": 258_000, "max_output_tokens": 8_192}
                ),
            )

            read_result = json.dumps({
                "outcome": "success",
                "code": "ok",
                "summary": "Read file",
                "data": {
                    "path": "runtime/eidos_runtime/runtime/engine.py",
                    "content": "class RuntimeEngine: ...",
                },
            })
            for index in range(2):
                item = store.create_tool_item(
                    current["id"], 1, index, f"read-{index}", "read_file",
                    '{"path":"runtime/eidos_runtime/runtime/engine.py"}',
                )
                store.complete_tool_item(item["id"], read_result)

            search = store.create_tool_item(
                current["id"], 1, 2, "search-1", "search_text", json.dumps({
                    "query": "RuntimeEngine",
                    "path": "runtime/eidos_runtime",
                    "regex": False,
                    "maxResults": 20,
                    "includeGlobs": ["*.py"],
                }, separators=(",", ":")),
            )
            store.complete_tool_item(search["id"], json.dumps({
                "outcome": "success",
                "code": "ok",
                "summary": "Searched text",
                "data": {"matches": [], "truncated": False},
            }))

            model = ScriptedModel([ModelResponse(text="done")])
            RuntimeEngine(store, model, lambda _message: None).run(
                current["id"], threading.Event()
            )

            assert store.read_run(current["id"])["status"] == "succeeded"
            summary = store.latest_compact_summary(current["id"])
            assert summary is not None
            assert len(summary.source_item_ids) == 251
            context = model.contexts[0]
            assert any(
                item.get("type") == "user"
                and "Compact summary:" in str(item.get("content"))
                for item in context
            )
            read_results = [
                item for item in context
                if item.get("type") == "tool_result"
                and item.get("name") == "read_file"
            ]
            assert len(read_results) == 2
            assert any("contextDeduplicated" in str(item.get("result"))
                       for item in read_results)
            search_call = next(
                item for item in context
                if item.get("type") == "tool_call"
                and item.get("name") == "search_text"
            )
            search_arguments = json.loads(str(search_call["arguments"]))
            assert search_arguments["path"] == "runtime/eidos_runtime"
            assert search_arguments["includeGlobs"] == ["*.py"]
        finally:
            store.close()


def test_recent_bounded_projection_can_exceed_soft_ceiling_without_being_dropped() -> None:
    with tempfile.TemporaryDirectory(prefix="eidos-protected-projection-") as temporary:
        root = Path(temporary)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        store = SessionStore(data)
        store.initialize()
        try:
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"],
                "Keep recent tool evidence",
                model_profile=default_profile_snapshot("deepseek-v4-flash").model_copy(
                    update={"context_window_tokens": 2_000_000, "max_output_tokens": 8_192}
                ),
            )
            bounded_result = json.dumps({
                "outcome": "success",
                "code": "ok",
                "summary": "Bounded recent evidence",
                "data": {"content": "x" * 60_000},
            })
            for index in range(16):
                item = store.create_tool_item(
                    run["id"], 1, index, f"recent-{index}", "read_file",
                    json.dumps({"path": f"recent-{index}.txt"}),
                )
                store.complete_tool_item(item["id"], bounded_result)

            model = ScriptedModel([ModelResponse(text="recent evidence retained")])
            RuntimeEngine(store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

            assert store.read_run(run["id"])["status"] == "succeeded"
            assert len([
                item for item in model.contexts[0]
                if item.get("type") == "tool_result"
            ]) == 16
        finally:
            store.close()


def test_context_projection_uses_bounded_result_instead_of_full_canonical_payload() -> None:
    with tempfile.TemporaryDirectory(prefix="eidos-bounded-result-") as temporary:
        root = Path(temporary)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        store = SessionStore(data)
        store.initialize()
        try:
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"],
                "Keep bounded model results",
                model_profile=default_profile_snapshot("deepseek-v4-flash").model_copy(
                    update={"context_window_tokens": 2_000_000, "max_output_tokens": 8_192}
                ),
            )
            canonical_result = json.dumps({
                "outcome": "success",
                "code": "ok",
                "summary": "Full canonical result",
                "data": {"content": "x" * 300_000},
            })
            model_result = json.dumps({
                "outcome": "success",
                "code": "ok",
                "summary": "Bounded model result",
                "data": {"path": "source.py"},
            })
            for index in range(4):
                item = store.create_tool_item(
                    run["id"], 1, index, f"bounded-{index}", "read_file",
                    json.dumps({"path": "source.py"}),
                )
                store.complete_tool_item(
                    item["id"], canonical_result, model_result_json=model_result
                )

            model = ScriptedModel([ModelResponse(text="bounded results retained")])
            RuntimeEngine(store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

            assert store.read_run(run["id"])["status"] == "succeeded"
            assert len([
                item for item in model.contexts[0]
                if item.get("type") == "tool_result"
            ]) == 4
        finally:
            store.close()
