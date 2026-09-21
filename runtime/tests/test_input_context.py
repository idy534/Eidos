from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.input_context import InputContextApplication
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.protocol.input_context import (
    DraftReadRequest,
    DraftWriteRequest,
    InputPrepareRequest,
    InputReadRequest,
)


def _application(
    tmp_path: Path,
    extensions: object | None = None,
) -> tuple[SessionStore, InputContextApplication, str, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    extension_api = extensions or SimpleNamespace(
        list_skills=lambda: SimpleNamespace(skills=()),
        list_plugins=lambda: SimpleNamespace(plugins=()),
        list_mcp_servers=lambda: SimpleNamespace(servers=()),
    )
    application = InputContextApplication(store, extension_api, lambda value: value)
    return store, application, str(session["id"]), workspace


def test_text_file_selection_is_persisted_and_read_with_line_bounds(
    tmp_path: Path,
) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    try:
        source = workspace / "notes.txt"
        source.write_text("one\ntwo\nthree\n", encoding="utf-8")

        prepared = application.prepare(InputPrepareRequest(
            sessionId=session_id,
            kind="file",
            source=str(source),
            startLine=2,
            endLine=3,
        ))
        preview = application.read(InputReadRequest(id=prepared.reference.id))

        assert prepared.reference.kind == "file"
        assert prepared.reference.status == "content"
        assert prepared.reference.label == "notes.txt:2-3"
        assert preview.text == "two\nthree\n"
        assert preview.reference == prepared.reference
    finally:
        store.close()


def test_directory_selection_is_location_only_and_bounded(tmp_path: Path) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    try:
        for index in range(201):
            (workspace / f"file-{index:03d}.txt").write_text(str(index), encoding="utf-8")

        prepared = application.prepare(InputPrepareRequest(
            sessionId=session_id,
            kind="directory",
            source=str(workspace),
        ))
        preview = application.read(InputReadRequest(id=prepared.reference.id))

        assert prepared.reference.status == "location"
        assert "未读取子文件内容" in preview.text
        assert "…目录摘要仅包含前 200 项" in preview.text
        assert "file-000.txt" in preview.text
    finally:
        store.close()


def test_image_selection_returns_a_bounded_thumbnail(tmp_path: Path) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    try:
        source = workspace / "diagram.png"
        Image.new("RGB", (2, 3), "red").save(source, format="PNG")

        prepared = application.prepare(InputPrepareRequest(
            sessionId=session_id,
            kind="image",
            source=str(source),
        ))
        preview = application.read(InputReadRequest(id=prepared.reference.id))

        assert prepared.reference.kind == "image"
        assert prepared.reference.status == "content"
        assert prepared.reference.size > 0
        assert preview.thumbnail is not None
        assert preview.thumbnail.startswith("data:image/jpeg;base64,")
    finally:
        store.close()


def test_excerpt_and_draft_round_trip_deduplicates_references(tmp_path: Path) -> None:
    store, application, session_id, _workspace = _application(tmp_path)
    try:
        prepared = application.prepare(InputPrepareRequest(
            sessionId=session_id,
            kind="excerpt",
            source="session:selection",
            text="Selected text",
        ))
        written = application.write_draft(DraftWriteRequest(
            key=session_id,
            text="Keep this draft",
            references=[prepared.reference.id, prepared.reference.id],
        ))
        restored = application.read_draft(DraftReadRequest(key=session_id))

        assert written.text == "Keep this draft"
        assert [value.id for value in written.references] == [prepared.reference.id]
        assert restored == written

        application.write_draft(DraftWriteRequest(key=session_id, text="", references=[]))
        assert application.read_draft(DraftReadRequest(key=session_id)).references == []
    finally:
        store.close()


def test_selection_validation_rejects_changed_skill_content(tmp_path: Path) -> None:
    skill = SimpleNamespace(
        qualified_id="user:demo",
        available=True,
        enabled=True,
        name="Demo",
        description="A demo skill",
        content_hash="a" * 64,
    )
    extensions = SimpleNamespace(
        list_skills=lambda: SimpleNamespace(skills=(skill,)),
        list_plugins=lambda: SimpleNamespace(plugins=()),
        list_mcp_servers=lambda: SimpleNamespace(servers=()),
    )
    store, application, session_id, _workspace = _application(tmp_path, extensions)
    try:
        prepared = application.prepare(InputPrepareRequest(
            sessionId=session_id,
            kind="skill",
            source="user:demo",
        ))
        snapshot = application.repository.read(prepared.reference.id)
        skill.content_hash = "b" * 64

        with pytest.raises(ApplicationError) as error:
            application.validate_selections([snapshot])
        assert error.value.code == "INVALID_STATE"
    finally:
        store.close()


def test_context_builder_projects_current_input_reference(tmp_path: Path) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    try:
        source = workspace / "prompt.txt"
        source.write_text("Use this context.\n", encoding="utf-8")
        prepared = application.prepare(InputPrepareRequest(
            sessionId=session_id,
            kind="file",
            source=str(source),
        ))
        run, _item = store.create_run(
            session_id,
            "Review the attached context.",
            references=[prepared.reference],
        )

        context = ContextBuilder(store).build(run["id"])
        reference_items = [
            item for item in context.model_context
            if item.get("sectionId") == f"input-reference:{prepared.reference.id}"
        ]

        assert len(reference_items) == 1
        assert "Use this context." in reference_items[0]["content"]
        assert prepared.reference.sha256 in reference_items[0]["content"]
    finally:
        store.close()
