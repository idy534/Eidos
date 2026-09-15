from __future__ import annotations

from pathlib import Path
import threading

from eidos_runtime.tools.declare_outputs import (
    DeclareOutputsAdapter,
    declare_outputs_entry,
)
from eidos_runtime.workspace.reader import capture_workspace_identity


def _adapter(workspace: Path) -> DeclareOutputsAdapter:
    return DeclareOutputsAdapter(capture_workspace_identity(workspace))


def test_declares_relative_and_absolute_files_with_verified_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = workspace / "report.md"
    slides = workspace / "slides.pptx"
    report.write_text("report\n", encoding="utf-8")
    slides.write_bytes(b"pptx")

    entry = declare_outputs_entry(capture_workspace_identity(workspace))
    result = _adapter(workspace).execute(
        {
            "outputs": [
                {"path": "report.md", "title": "Report"},
                {"path": str(slides)},
            ]
        },
        threading.Event(),
    )

    assert entry.spec.name == "declare_outputs"
    assert entry.provenance.source_id == "eidos.declare-outputs"
    assert result["outcome"] == "success"
    assert result["data"]["executionRoot"] == str(workspace.resolve())
    assert result["data"]["outputs"] == [
        {
            "path": "report.md",
            "title": "Report",
            "sizeBytes": report.stat().st_size,
            "version": result["data"]["outputs"][0]["version"],
        },
        {
            "path": "slides.pptx",
            "sizeBytes": slides.stat().st_size,
            "version": result["data"]["outputs"][1]["version"],
        },
    ]
    assert all(len(output["version"]) == 64 for output in result["data"]["outputs"])


def test_duplicate_paths_use_the_last_title_and_do_not_duplicate_outputs(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.md").write_text("report\n", encoding="utf-8")

    result = _adapter(workspace).execute(
        {
            "outputs": [
                {"path": "report.md", "title": "Draft"},
                {"path": "report.md", "title": "Final"},
            ]
        },
        threading.Event(),
    )

    assert result["outcome"] == "success"
    assert result["data"]["outputs"] == [
        {
            "path": "report.md",
            "title": "Final",
            "sizeBytes": len("report\n".encode()),
            "version": result["data"]["outputs"][0]["version"],
        }
    ]


def test_a_failed_file_check_publishes_no_partial_declaration(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "ok.txt").write_text("ok\n", encoding="utf-8")

    result = _adapter(workspace).execute(
        {"outputs": [{"path": "ok.txt"}, {"path": "missing.txt"}]},
        threading.Event(),
    )

    assert result == {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "declare_outputs",
        "outcome": "error",
        "code": "file_unavailable",
        "summary": "Outputs were not declared: file_unavailable",
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


def test_workspace_boundary_and_special_file_checks_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(outside)
    (workspace / "regular.txt").write_text("regular\n", encoding="utf-8")
    (workspace / "hardlink.txt").hardlink_to(workspace / "regular.txt")

    escaped = _adapter(workspace).execute(
        {"outputs": [{"path": "escape.txt"}]}, threading.Event()
    )
    hardlink = _adapter(workspace).execute(
        {"outputs": [{"path": "hardlink.txt"}]}, threading.Event()
    )
    sensitive = _adapter(workspace).execute(
        {"outputs": [{"path": ".git/config"}]}, threading.Event()
    )

    assert escaped["code"] == "workspace_boundary_violation"
    assert hardlink["code"] == "unsupported_file_hardlink"
    assert sensitive["code"] == "sensitive_path"
