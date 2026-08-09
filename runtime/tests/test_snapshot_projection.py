from __future__ import annotations

import json

from eidos_runtime.db.mappers import _snapshot_display_arguments  # noqa: PLC2701


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
            "timeoutSeconds": 10,
            "additionalPermissions": {
                "network": {"enabled": True},
            },
        }),
    }) or "null")

    assert write_arguments == {"path": "notes.txt"}
    assert shell_arguments == {
        "command": "printf 'safe'",
        "cwd": ".",
        "timeoutSeconds": 10,
    }


def test_snapshot_glob_projection_is_bounded() -> None:
    projected = json.loads(_snapshot_display_arguments({
        "toolName": "search_text",
        "argumentsJson": json.dumps({
            "query": "needle",
            "includeGlobs": [f"file-{index}.rs" for index in range(100)],
        }),
    }) or "null")

    assert len(projected["includeGlobs"]) <= 32

