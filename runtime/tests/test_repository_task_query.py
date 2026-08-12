from __future__ import annotations

import json
from pathlib import Path

from eidos_runtime.context.facts import ContextFacts, ContextItemFact
from eidos_runtime.repo_intelligence.index import RepositoryIndexer
from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder
from eidos_runtime.repo_intelligence.query import RepositoryTaskQueryBuilder


def test_task_query_grounds_paths_symbols_and_tool_history(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    source = root / "runtime" / "engine.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class RuntimeEngine:\n    pass\n\ndef authenticate_user():\n    pass\n",
        encoding="utf-8",
    )
    test_file = root / "tests" / "test_auth.py"
    test_file.parent.mkdir()
    test_file.write_text("authenticate_user()\n", encoding="utf-8")
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)
    facts = ContextFacts(
        run_id="run-1",
        session_id="session-1",
        items=(
            ContextItemFact(
                item_id="read-item",
                run_id="old-run",
                kind="tool_call",
                status="completed",
                provider_call_id="read-call",
                tool_name="read_file",
                arguments_json=json.dumps({"path": "tests/test_auth.py"}),
                result_json=json.dumps({
                    "data": {"path": "tests/test_auth.py"}
                }),
                ordinal=1,
            ),
            ContextItemFact(
                item_id="search-item",
                run_id="run-1",
                kind="tool_call",
                status="completed",
                provider_call_id="search-call",
                tool_name="search_text",
                arguments_json=json.dumps({"query": "auth"}),
                result_json=json.dumps({
                    "data": {"matches": [{"path": "runtime/engine.py"}]}
                }),
                ordinal=2,
            ),
        ),
        committed_workspace_changes=("runtime/engine.py", "invented.py"),
    )

    query = RepositoryTaskQueryBuilder().build(
        "修改 RuntimeEngine 和 authenticate_user，见 engine.py；不要碰 missing.py",
        inventory=inventory,
        index=index,
        facts=facts,
        dirty_paths=("tests/test_auth.py", "unknown.py"),
    )

    assert query.text.startswith("修改 RuntimeEngine")
    assert query.mentioned_paths == ("runtime/engine.py",)
    assert query.mentioned_symbols == ("RuntimeEngine", "authenticate_user")
    assert query.previous_read_paths == ("tests/test_auth.py",)
    assert query.recent_tool_result_paths == (
        "runtime/engine.py",
        "tests/test_auth.py",
    )
    assert query.recently_modified_paths == (
        "runtime/engine.py",
        "tests/test_auth.py",
    )
    assert "missing.py" not in query.mentioned_paths
    assert "invented.py" not in query.recently_modified_paths
