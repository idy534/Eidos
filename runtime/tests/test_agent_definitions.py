import hashlib
import json
import os
from pathlib import Path
import time
import tomllib

import pytest
from pydantic import ValidationError

from eidos_runtime.application.agent_catalog import AgentCatalog
from eidos_runtime.application.agent_definitions import AgentDefinitionsApplication
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.domain.agents import AgentDefinition, AgentModelSelection
from eidos_runtime.infrastructure import local_files
from eidos_runtime.infrastructure.agent_definition_files import (
    encode_definition,
    AgentDefinitionFileError,
)
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.protocol.collaboration import (
    AgentDefinitionDocument,
    AgentDefinitionReadRequest,
    AgentDefinitionRemoveRequest,
    AgentDefinitionSaveRequest,
)


@pytest.fixture(autouse=True, scope="module")
def _runtime_file_owner() -> None:
    if local_files.WINDOWS:
        from eidos_runtime.infrastructure.windows_files import (
            configure_process_file_owner,
        )

        configure_process_file_owner()


@pytest.fixture
def management(tmp_path):
    data = tmp_path / "data"
    models = ModelConfigStore(data)
    models.initialize()
    models.create(
        provider_id="volcengine", model_id="deepseek-v4.1-flash", api_key="fixture-key"
    )
    return AgentDefinitionsApplication(AgentCatalog(data, models))


def definition(**changes):
    return AgentDefinition(
        schema_version=1,
        name="luna-work",
        description="负责实现",
        instructions='约束：\n保留 "引号"、\\路径、\t缩进和中文。😀',
        **changes,
    )


def save(app, value=None, original=None):
    return app.save(
        AgentDefinitionSaveRequest(
            definition=value or definition(),
            source=original.source if original else None,
            expected_version=original.version if original else None,
        )
    )


def test_create_read_edit_remove_and_reload_share_manual_toml(management):
    app = management
    document = save(app)
    path = Path(document.source)
    assert path.parent == app.catalog.root
    assert local_files.has_private_access(path, path.stat(), 0o600)
    assert (
        tomllib.loads(path.read_text(encoding="utf-8"))["instructions"]
        == document.definition.instructions
    )
    assert app.read(AgentDefinitionReadRequest(source=document.source)) == document
    second = save(app, definition().model_copy(update={"name": "luna-two"}))
    assert Path(second.source).exists()
    app.remove(
        AgentDefinitionRemoveRequest(
            source=second.source, expected_version=second.version
        )
    )
    path.rename(path.with_name("manual-file.toml"))
    path = path.with_name("manual-file.toml")
    path.write_text(
        path.read_text(encoding="utf-8") + "# 手工编辑的注释\n",
        encoding="utf-8",
        newline="\n",
    )
    original = app.read(AgentDefinitionReadRequest(source=str(path)))
    changed = save(
        app,
        original.definition.model_copy(
            update={"description": "更新说明", "enabled": False}
        ),
        original,
    )
    assert changed.source == str(path)
    assert not app.catalog.list().agents[0].enabled
    assert changed.version != original.version
    app.remove(
        AgentDefinitionRemoveRequest(
            source=changed.source, expected_version=changed.version
        )
    )
    assert app.catalog.list().agents == []
    assert not path.exists()


def test_edit_and_remove_reject_external_changes_even_comments(management):
    document = save(management)
    path = Path(document.source)
    path.write_text(
        document.text + "# 外部变化\n", encoding="utf-8", newline="\n"
    )
    raw = path.read_bytes()
    with pytest.raises(ApplicationError, match="已经变化") as failure:
        save(management, original=document)
    assert failure.value.code == "AGENT_DEFINITION_CONFLICT"
    with pytest.raises(ApplicationError, match="已经变化"):
        management.remove(
            AgentDefinitionRemoveRequest(
                source=document.source, expected_version=document.version
            )
        )
    assert path.read_bytes() == raw


def test_create_rejects_duplicate_definition_in_differently_named_disabled_file(
    management,
):
    original = save(management, definition(enabled=False))
    path = Path(original.source)
    path.rename(path.with_name("other-file.toml"))
    with pytest.raises(ApplicationError, match="名称已经存在"):
        save(management)
    assert len(management.catalog.list().agents) == 1


def test_edit_requires_version_and_preserves_identity(management):
    original = save(management)
    with pytest.raises(ApplicationError, match="原文件和版本"):
        management.save(
            AgentDefinitionSaveRequest(definition=definition(), source=original.source)
        )
    with pytest.raises(ApplicationError, match="名称不能修改"):
        save(
            management,
            original.definition.model_copy(update={"name": "renamed"}),
            original,
        )
    assert Path(original.source).read_text(encoding="utf-8") == original.text


@pytest.mark.parametrize(
    "name", ["../escape", "worker", "explorer", "default", "verifier", "With-Caps"]
)
def test_reserved_and_invalid_names_are_rejected(name):
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate({**definition().model_dump(), "name": name})


def test_provider_and_reasoning_validation_and_disabled_repair(management):
    selected = definition(
        model=AgentModelSelection(
            provider_id="volcengine",
            config_id="deepseek-v4.1-flash",
            reasoning_selection="high",
        )
    )
    original = save(management, selected)
    assert tomllib.loads(original.text)["model"]["provider_id"] == "volcengine"
    mismatch = selected.model_copy(
        update={"model": selected.model.model_copy(update={"provider_id": "deepseek"})}
    )
    with pytest.raises(ApplicationError, match="提供商与模型配置不匹配"):
        save(management, mismatch, original)
    invalid = selected.model_copy(
        update={
            "model": AgentModelSelection(provider_id="volcengine", config_id="missing")
        }
    )
    with pytest.raises(ApplicationError, match="模型尚未配置"):
        save(management, invalid, original)
    disabled = save(management, invalid.model_copy(update={"enabled": False}), original)
    assert not disabled.definition.enabled


@pytest.mark.parametrize("operation", ["read", "edit", "remove"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_symlink_hardlink_and_outside_sources_never_modify_targets(
    management, tmp_path, operation, kind
):
    original = save(management)
    target = tmp_path / "outside.toml"
    target.write_text(original.text, encoding="utf-8", newline="\n")
    path = Path(original.source)
    path.unlink()
    if kind == "symlink":
        try:
            path.symlink_to(target)
        except OSError as error:
            if local_files.WINDOWS and getattr(error, "winerror", None) == 1314:
                pytest.skip(
                    "Windows requires SeCreateSymbolicLinkPrivilege or Developer Mode for file symlinks"
                )
            raise
    else:
        os.link(target, path)
    with pytest.raises(ApplicationError):
        if operation == "read":
            management.read(AgentDefinitionReadRequest(source=str(path)))
        elif operation == "edit":
            save(management, original=original)
        else:
            management.remove(
                AgentDefinitionRemoveRequest(
                    source=str(path), expected_version=original.version
                )
            )
    assert target.read_text(encoding="utf-8") == original.text
    with pytest.raises(ApplicationError, match="路径无效"):
        management.read(AgentDefinitionReadRequest(source=str(target)))


def test_directory_symlink_and_large_encoded_configuration_fail_closed(
    management, tmp_path
):
    external = tmp_path / "external"
    external.mkdir()
    if local_files.WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(external), str(management.catalog.root))
    else:
        management.catalog.root.symlink_to(external)
    with pytest.raises(ApplicationError):
        save(management)
    assert list(external.iterdir()) == []
    with pytest.raises(AgentDefinitionFileError, match="64 KiB"):
        encode_definition(
            definition().model_copy(update={"instructions": "\n" * (48 * 1024)})
        )


def test_management_keeps_previous_catalog_snapshot_unchanged(management):
    original = save(management)
    frozen, _ = management.catalog.scan()
    changed = save(
        management,
        original.definition.model_copy(update={"instructions": "新的约束"}),
        original,
    )
    assert frozen.definitions[0].instructions == original.definition.instructions
    assert (
        management.catalog.scan()[0].definitions[0].instructions
        == changed.definition.instructions
    )


def test_locked_directory_does_not_block_runtime(management):
    original = save(management)
    descriptor = (
        local_files.open_file(
            management.catalog.root.parent / ".agent-definitions.lock",
            os.O_CREAT | os.O_RDWR,
        )
        if local_files.WINDOWS
        else local_files.open_directory(management.catalog.root)
    )
    try:
        local_files.lock_exclusive(descriptor)
        with pytest.raises(ApplicationError) as failure:
            save(management, original=original)
        assert failure.value.code == "AGENT_DEFINITION_BUSY"
    finally:
        os.close(descriptor)
    assert Path(original.source).read_text(encoding="utf-8") == original.text


def test_failed_fsync_reports_possible_write_without_retry(management, monkeypatch):
    original = save(management)

    def fail_sync(_descriptor):
        raise OSError("fixture write failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(ApplicationError) as failure:
        save(
            management,
            original.definition.model_copy(update={"instructions": "已实际写入"}),
            original,
        )
    assert failure.value.code == "AGENT_DEFINITION_WRITE_UNCERTAIN"
    assert (
        tomllib.loads(Path(original.source).read_text(encoding="utf-8"))["instructions"]
        == "已实际写入"
    )


def test_edit_succeeds_after_delay_and_rejects_external_replacement(
    management, tmp_path, monkeypatch
):
    created = save(management)
    time.sleep(0.05)
    edited = save(
        management,
        created.definition.model_copy(update={"description": "延时后编辑"}),
        created,
    )
    assert edited.definition.description == "延时后编辑"
    time.sleep(0.05)
    edited_again = save(
        management,
        edited.definition.model_copy(update={"instructions": "再次更新指令"}),
        edited,
    )
    assert edited_again.definition.instructions == "再次更新指令"

    replacement = tmp_path / "replacement.toml"
    replacement.write_text(edited_again.text, encoding="utf-8", newline="\n")
    replacement_stat = replacement.lstat()
    monkeypatch.setattr(local_files, "lstat_at", lambda *_args: replacement_stat)
    with pytest.raises(ApplicationError) as failure:
        save(
            management,
            edited_again.definition.model_copy(update={"description": "并发替换冲突"}),
            edited_again,
        )
    assert failure.value.code == "AGENT_DEFINITION_CONFLICT"
    assert (
        Path(edited_again.source).read_text(encoding="utf-8") == edited_again.text
    )


def test_management_fixture_uses_same_strict_wire_contract():
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2] / "protocol/fixtures/custom-agents.json"
        ).read_text(encoding="utf-8")
    )
    request = AgentDefinitionSaveRequest.model_validate(fixture["management"]["save"])
    document = AgentDefinitionDocument.model_validate(fixture["management"]["document"])
    assert request.definition == document.definition
    assert hashlib.sha256(document.text.encode()).hexdigest() == document.version
    with pytest.raises(ValidationError):
        AgentDefinitionSaveRequest.model_validate(fixture["management"]["invalidSave"])
