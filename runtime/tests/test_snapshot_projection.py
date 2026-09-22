from __future__ import annotations

import json

from eidos_runtime.db.mappers import _snapshot_display_arguments  # noqa: PLC2701
from eidos_runtime.protocol.tool_text import project_tool_text


def test_discovery_snapshot_keeps_scoped_arguments_and_bounded_globs() -> None:
    list_arguments = json.loads(_snapshot_display_arguments({
        "toolName": "list_files",
        "argumentsJson": json.dumps({
            "path": "codex-rs/core",
            "maxDepth": 2,
            "maxEntries": 50,
            "ignored": "not projected",
        }),
    }) or "null")
    search_arguments = json.loads(_snapshot_display_arguments({
        "toolName": "search_text",
        "argumentsJson": json.dumps({
            "query": "ConfigBuilder",
            "path": "codex-rs/core",
            "regex": True,
            "maxResults": 50,
            "includeGlobs": ["*.rs", "core/**/*.py"],
            "ignored": "not projected",
        }),
    }) or "null")

    assert list_arguments == {
        "maxDepth": 2,
        "maxEntries": 50,
        "path": "codex-rs/core",
    }
    assert search_arguments == {
        "includeGlobs": ["*.rs", "core/**/*.py"],
        "maxResults": 50,
        "path": "codex-rs/core",
        "query": "ConfigBuilder",
        "regex": True,
    }


def test_snapshot_does_not_project_write_content_or_shell_permissions() -> None:
    write_arguments = json.loads(_snapshot_display_arguments({
        "toolName": "write_file",
        "argumentsJson": json.dumps({
            "path": "notes.txt",
            "content": "secret content",
        }),
    }) or "null")
    shell_arguments = json.loads(_snapshot_display_arguments({
        "toolName": "run_shell",
        "argumentsJson": json.dumps({
            "command": "printf 'safe'",
            "cwd": ".",
            "yieldTimeMs": 10_000,
            "additionalPermissions": {
                "network": {"enabled": True},
            },
        }),
    }) or "null")

    assert write_arguments == {"path": "notes.txt"}
    assert shell_arguments == {
        "command": "printf 'safe'",
        "cwd": ".",
        "yieldTimeMs": 10_000,
    }


def test_display_projection_redacts_command_and_search_text_only() -> None:
    command = "printf 'Bearer abcdefghijklmnop'"
    query = "token=supersecret"
    item = {
        "toolCall": {
            "toolName": "run_shell",
            "argumentsJson": json.dumps({"command": command, "cwd": "."}),
        }
    }
    search_item = {
        "toolCall": {
            "toolName": "search_text",
            "argumentsJson": json.dumps({"query": query, "path": "."}),
        }
    }

    projected = project_tool_text(item)
    projected_search = project_tool_text(search_item)

    assert json.loads(item["toolCall"]["argumentsJson"]) == {
        "command": command,
        "cwd": ".",
    }
    assert json.loads(projected["toolCall"]["argumentsJson"])["command"] == (
        "printf 'Bearer [REDACTED:bearer_token]'"
    )
    assert json.loads(projected_search["toolCall"]["argumentsJson"])["query"] == (
        "[REDACTED:secret_assignment]"
    )


def test_snapshot_glob_projection_is_bounded() -> None:
    projected = json.loads(_snapshot_display_arguments({
        "toolName": "search_text",
        "argumentsJson": json.dumps({
            "query": "needle",
            "includeGlobs": [f"file-{index}.rs" for index in range(100)],
        }),
    }) or "null")

    assert len(projected["includeGlobs"]) <= 32


def test_snapshot_preserves_planning_tool_arguments() -> None:
    questions_input = [
        {
            "id": "q1",
            "question": "Which component to refactor?",
            "type": "single_select",
            "options": [
                {"id": "opt1", "label": "ExecutionFeed", "description": "UI feed"},
                {"id": "opt2", "label": "WorkspaceDock", "description": "Side dock"},
            ],
            "recommendedOptionId": "opt1",
        }
    ]
    input_projected = json.loads(_snapshot_display_arguments({
        "toolName": "request_user_input",
        "argumentsJson": json.dumps({
            "questions": questions_input,
            "extra_field": "ignored",
        }),
    }) or "null")

    assert input_projected == {
        "questions": [
            {
                "id": "q1",
                "question": "Which component to refactor?",
                "type": "single_select",
                "options": [
                    {"id": "opt1", "label": "ExecutionFeed", "description": "UI feed"},
                    {"id": "opt2", "label": "WorkspaceDock", "description": "Side dock"},
                ],
                "recommendedOptionId": "opt1",
            }
        ]
    }

    plan_projected = json.loads(_snapshot_display_arguments({
        "toolName": "write_plan",
        "argumentsJson": json.dumps({
            "title": "系统重构计划",
            "markdown": "# 详尽计划正文，不应包含在快照参数中",
            "planId": "plan-123",
            "expectedRevision": 1,
            "readyForReview": True,
        }),
    }) or "null")

    assert plan_projected == {
        "expectedRevision": 1,
        "planId": "plan-123",
        "readyForReview": True,
        "title": "系统重构计划",
    }


def test_clarification_projection_uses_schema_defaults_and_rejects_invalid_questions() -> None:
    question = {"id": "q1", "question": "补充要求？", "type": "text"}
    def project(questions):
        return _snapshot_display_arguments({
            "toolName": "request_user_input",
            "argumentsJson": json.dumps({"questions": questions}),
        })

    projected = json.loads(project([question]) or "null")
    assert projected["questions"][0]["options"] == []
    assert project([question, question]) is None
    assert project([{**question, "id": "x" * 81}]) is None
    assert project([{**question, "question": "海" * 1001}]) is None
    assert project([{"id": "q", "question": "Choose", "options": []}]) is None
    assert project([None]) is None
    assert project([{**question, "id": str(index)} for index in range(4)]) is None
    assert project([{
        "id": "q", "question": "Choose", "options": [
            {"id": str(index), "label": "Option"} for index in range(7)
        ],
    }]) is None


def test_snapshot_rejects_boolean_values_for_numeric_display_arguments() -> None:
    projected = json.loads(_snapshot_display_arguments({
        "toolName": "write_plan",
        "argumentsJson": json.dumps({"title": True, "expectedRevision": True, "readyForReview": True}),
    }) or "null")
    assert projected == {"readyForReview": True}
