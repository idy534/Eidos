from __future__ import annotations

import json
from pathlib import Path

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.extensions import (
    DeferredPluginImport,
    ExtensionApplication,
    PluginImportCompleted,
    PluginImportFailure,
)
from eidos_runtime.application.models import (
    ModelProfileApplication,
    ModelProfileDraft,
)
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.model_gateway.auth import ModelSecretStore


class _LegacyModelRuntime:
    def __init__(self, configuration: ModelConfigStore) -> None:
        self.configuration = configuration
        self.configured = False
        self.configured_keys: list[str] = []

    def has_configured_legacy_model(self) -> bool:
        return self.configured

    def configure_legacy_model(self, api_key: str) -> None:
        self.configuration.save_api_key(api_key)
        self.configured_keys.append(api_key)
        self.configured = True


class _NotCancelled:
    def is_set(self) -> bool:
        return False


def _profile_draft(*, name: str = "Primary") -> ModelProfileDraft:
    return ModelProfileDraft.from_wire({
        "name": name,
        "provider": "deepseek",
        "modelId": "deepseek-v4-pro",
        "contextWindow": 128_000,
        "maxOutputTokens": 8_192,
    })


def _plugin_source(root: Path) -> Path:
    source = root / "plugin-source"
    (source / "skills" / "review").mkdir(parents=True)
    (source / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review files.\n---\nInspect first.\n",
        encoding="utf-8",
    )
    (source / "plugin.json").write_text(json.dumps({
        "schemaVersion": 1,
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        "description": "Fixture",
        "skills": [{"root": "skills/review"}],
        "mcpServers": [{
            "id": "fixture",
            "executable": "python3",
            "argv": [],
            "envNames": [],
            "permissionProfile": "workspace_read",
            "startupTimeoutSeconds": 5,
            "toolTimeoutSeconds": 10,
            "enabled": True,
        }],
    }), encoding="utf-8")
    return source


def test_model_profile_application_owns_profile_crud_capability_selection_and_configure(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    configuration = ModelConfigStore(data)
    configuration.initialize()
    secrets = ModelSecretStore(data)
    secrets.initialize()
    runtime = _LegacyModelRuntime(configuration)
    application = ModelProfileApplication(
        store=store,
        secret_store=secrets,
        model_config=configuration,
        runtime=runtime,
    )
    try:
        created = application.create_profile(
            _profile_draft(), api_key="sk-profile-secret-value-1234567890"
        )

        assert application.get_profile(created.id) == created
        assert application.list_profiles().profiles == (created,)
        assert application.list_models().default_model_id == created.id
        profile_option = application.list_models().models[0]
        assert profile_option.id == created.id
        assert profile_option.selectable is True

        updated = application.update_profile(
            created.id,
            _profile_draft(name="Renamed"),
        )
        assert updated.id == created.id
        assert updated.name == "Renamed"

        configured = application.configure("sk-legacy-secret-value-1234567890")
        assert configured.configured is True
        assert runtime.configured_keys == ["sk-legacy-secret-value-1234567890"]

        deleted = application.delete_profile(created.id)
        assert deleted.deleted_profile_id == created.id
        with pytest.raises(ApplicationError) as missing:
            application.get_profile(created.id)
        assert missing.value.code == "RESOURCE_NOT_FOUND"
    finally:
        store.close()


def test_model_profile_application_cleans_new_secret_when_profile_is_invalid(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    configuration = ModelConfigStore(data)
    configuration.initialize()
    secrets = ModelSecretStore(data)
    secrets.initialize()
    application = ModelProfileApplication(
        store=store,
        secret_store=secrets,
        model_config=configuration,
        runtime=_LegacyModelRuntime(configuration),
    )
    invalid = ModelProfileDraft.from_wire({
        "name": "Invalid",
        "provider": "deepseek",
        "modelId": "deepseek-v4-pro",
        "contextWindow": -1,
        "maxOutputTokens": 8_192,
    })
    try:
        with pytest.raises(ApplicationError) as invalid_profile:
            application.create_profile(
                invalid, api_key="sk-orphan-secret-value-1234567890"
            )
        assert invalid_profile.value.code == "INVALID_PARAMS"

        assert secrets.path is not None
        assert json.loads(secrets.path.read_text(encoding="utf-8")) == {}
        assert application.list_profiles().profiles == ()
    finally:
        store.close()


def test_extension_application_preserves_deferred_import_and_typed_operations(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    catalog = PluginCatalog(store)
    application = ExtensionApplication(store=store, plugins=catalog)
    source = _plugin_source(tmp_path)
    operation_id = "00000000-0000-4000-8000-000000000001"
    try:
        deferred = application.prepare_plugin_import(
            source_path=source,
            operation_id=operation_id,
            request_id="client-plugin-import",
        )
        assert isinstance(deferred, DeferredPluginImport)
        assert application.list_plugins().plugins == ()

        imported = deferred.run(_NotCancelled())
        assert isinstance(imported, PluginImportCompleted)
        assert imported.plugin.id == "demo"
        replay = application.prepare_plugin_import(
            source_path=source,
            operation_id=operation_id,
            request_id="client-plugin-import-replay",
        )
        assert replay == imported

        enabled = application.set_plugin_enabled(
            plugin_id="demo",
            enabled=True,
            operation_id="00000000-0000-4000-8000-000000000002",
        )
        assert enabled.enabled is True
        assert application.list_skills().skills[0].qualified_id == "demo:review"

        mcp = application.set_mcp_enabled(
            plugin_id="demo",
            server_id="fixture",
            enabled=True,
            consent=True,
            operation_id="00000000-0000-4000-8000-000000000003",
        )
        assert mcp.consented is True
        assert application.read_extensions().through_event_id > 0
        assert application.read_extension_events().items

        removed = application.remove_plugin(
            plugin_id="demo",
            operation_id="00000000-0000-4000-8000-000000000004",
        )
        assert removed.status == "removed"
    finally:
        store.close()


def test_extension_application_maps_unavailable_to_a_stable_code(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    unavailable = ExtensionApplication(store=store, plugins=None)
    try:
        with pytest.raises(ApplicationError, match="EXTENSIONS_UNAVAILABLE"):
            unavailable.list_plugins()
    finally:
        store.close()


def test_deferred_plugin_import_persists_a_failure_for_idempotent_replay(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    application = ExtensionApplication(store=store, plugins=PluginCatalog(store))
    operation_id = "00000000-0000-4000-8000-000000000099"
    try:
        deferred = application.prepare_plugin_import(
            source_path=tmp_path / "missing-plugin",
            operation_id=operation_id,
            request_id="client-plugin-import",
        )
        assert isinstance(deferred, DeferredPluginImport)

        outcome = deferred.run(_NotCancelled())
        assert outcome == PluginImportFailure("PLUGIN_IMPORT_REJECTED")
        with pytest.raises(ApplicationError) as replay:
            application.prepare_plugin_import(
                source_path=tmp_path / "missing-plugin",
                operation_id=operation_id,
                request_id="client-plugin-import-replay",
            )
        assert replay.value.code == "PLUGIN_IMPORT_REJECTED"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("after_event_id", "limit"),
    (("0", 200), (1.5, 200), (0, "200"), (0, 1.5)),
)
def test_extension_events_reject_non_integer_application_inputs(
    tmp_path: Path, after_event_id: object, limit: object
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    application = ExtensionApplication(store=store, plugins=None)
    try:
        with pytest.raises(ApplicationError) as invalid:
            application.read_extension_events(
                after_event_id=after_event_id,  # type: ignore[arg-type]
                limit=limit,  # type: ignore[arg-type]
            )
        assert invalid.value.code == "INVALID_PARAMS"
    finally:
        store.close()
