from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest

from eidos_runtime.model.config import (
    MODEL_CATALOG,
    ModelConfigError,
    ModelConfigStore,
    model_presets,
)


def _store(tmp_path: Path) -> ModelConfigStore:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = ModelConfigStore(data)
    store.initialize()
    return store


def test_model_presets_only_expose_the_supported_catalog() -> None:
    presets = model_presets()

    assert [provider["id"] for provider in presets["providers"]] == [
        "deepseek",
        "minimax",
        "kimi",
        "volcengine",
    ]
    assert [
        model["id"]
        for provider in presets["providers"]
        for model in provider["models"]
    ] == [
        "deepseek-flash",
        "MiniMax-M3",
        "kimi-k3",
        "kimi-k2.7-code-highspeed",
        "deepseek-v4-pro-ga-260813",
        "deepseek-v4-flash-ga-260731",
        "glm-5.3",
        "glm-5.3-flash",
        "minimax-m3",
    ]
    assert all(
        model["url"].endswith("/chat/completions")
        for provider in presets["providers"]
        for model in provider["models"]
    )


def test_model_catalog_declares_each_model_reasoning_choices_and_default() -> None:
    presets = model_presets()
    models = {
        model["id"]: model
        for provider in presets["providers"]
        for model in provider["models"]
    }
    expected = {
        "deepseek-flash": (True, "high", ["none", "low", "high", "max"]),
        "MiniMax-M3": (True, "thinking", ["none", "thinking"]),
        "kimi-k3": (True, "max", ["low", "high", "max"]),
        "kimi-k2.7-code-highspeed": (True, None, None),
        "deepseek-v4-pro-ga-260813": (True, "high", ["none", "low", "high", "max"]),
        "deepseek-v4-flash-ga-260731": (True, "high", ["none", "low", "high", "max"]),
        "glm-5.3": (True, None, None),
        "glm-5.3-flash": (True, "max", ["low", "high", "max"]),
        "minimax-m3": (False, None, None),
    }

    assert set(models) == set(expected)
    for model_id, (supports_reasoning, default, selections) in expected.items():
        model = models[model_id]
        assert model["supportsReasoning"] is supports_reasoning
        if default is None:
            assert model["reasoning"] is None
            continue
        assert model["reasoning"] == {
            "defaultSelection": default,
            "selections": selections,
        }


@pytest.mark.parametrize(
    ("legacy_id", "legacy_name"),
    [
        ("deepseek-v4-flash", "DeepSeek-V4 Flash"),
        ("deepseek-v4-pro", "DeepSeek-V4 Pro"),
    ],
)
def test_legacy_deepseek_model_id_is_migrated_to_the_current_id(
    tmp_path: Path,
    legacy_id: str,
    legacy_name: str,
) -> None:
    store = _store(tmp_path)
    store.path.write_text(
        json.dumps([
            {
                "id": legacy_id,
                "name": legacy_name,
                "vendor": "DeepSeek",
                "url": "https://api.deepseek.com/chat/completions",
                "apiKey": "sk-deepseek-secret-value",
                "supportsToolCall": True,
                "supportsImages": False,
                "supportsReasoning": True,
                "reasoning": {
                    "defaultEffort": "high",
                    "supportedEfforts": ["high", "max"],
                },
            }
        ]),
        encoding="utf-8",
    )
    store.path.chmod(0o600)

    store.initialize()

    assert [model.id for model in store.list()] == ["deepseek-flash"]
    migrated = store.get(legacy_id)
    assert migrated is not None
    assert migrated.id == "deepseek-flash"
    assert migrated.api_key == "sk-deepseek-secret-value"
    assert migrated.reasoning is not None
    assert migrated.reasoning.default_selection == "high"
    assert migrated.reasoning.selections == ("high", "max")
    assert json.loads(store.path.read_text(encoding="utf-8"))[0]["id"] == "deepseek-flash"


@pytest.mark.parametrize(
    ("default_key", "supported_key"),
    [
        ("defaultEffort", "supportedEfforts"),
        ("default_effort", "supported_efforts"),
    ],
)
def test_old_models_json_reasoning_metadata_is_preserved_with_api_key(
    tmp_path: Path,
    default_key: str,
    supported_key: str,
) -> None:
    store = _store(tmp_path)
    store.path.write_text(
        json.dumps([
            {
                "id": "MiniMax-M3",
                "name": "MiniMax M3",
                "vendor": "MiniMax",
                "url": "https://api.minimaxi.com/v1/chat/completions",
                "apiKey": "minimax-secret-value",
                "supportsToolCall": True,
                "supportsImages": False,
                "supportsReasoning": True,
                "reasoning": {
                    default_key: "high",
                    supported_key: ["high", "max"],
                },
            }
        ]),
        encoding="utf-8",
    )
    store.path.chmod(0o600)

    store.initialize()

    model = store.get("MiniMax-M3")
    assert model is not None
    assert model.api_key == "minimax-secret-value"
    assert model.reasoning is not None
    assert model.reasoning.default_selection == "high"
    assert model.reasoning.selections == ("high", "max")
    persisted = json.loads(store.path.read_text(encoding="utf-8"))[0]
    assert persisted["apiKey"] == "minimax-secret-value"
    assert persisted["reasoning"] == {
        default_key: "high",
        supported_key: ["high", "max"],
    }
    assert "reasoningSelection" not in persisted


def test_removed_volcengine_glm_52_config_migrates_to_glm_53(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.write_text(
        json.dumps([
            {
                "id": "glm-5-2-260617",
                "name": "GLM 5.2",
                "vendor": "Volcengine",
                "url": "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                "apiKey": "volcengine-secret-value",
                "supportsToolCall": True,
                "supportsImages": False,
                "supportsReasoning": False,
            }
        ]),
        encoding="utf-8",
    )
    store.path.chmod(0o600)

    store.initialize()

    models = store.list()
    assert [(model.id, model.name, model.api_key) for model in models] == [
        ("glm-5.3", "GLM 5.2", "volcengine-secret-value")
    ]
    assert json.loads(store.path.read_text(encoding="utf-8"))[0]["id"] == "glm-5.3"


def test_glm_52_migration_conflict_preserves_both_saved_models(tmp_path: Path) -> None:
    store = _store(tmp_path)
    common = {
        "vendor": "Volcengine",
        "url": "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
        "apiKey": "volcengine-secret-value",
        "supportsToolCall": True,
        "supportsImages": False,
        "supportsReasoning": False,
    }
    payload = json.dumps([
        {**common, "id": "glm-5-2-260617", "name": "GLM 5.2"},
        {**common, "id": "glm-5.3", "name": "GLM 5.3"},
    ])
    store.path.write_text(payload, encoding="utf-8")
    store.path.chmod(0o600)

    with pytest.raises(ModelConfigError, match="migration conflict"):
        store.initialize()

    assert store.path.read_text(encoding="utf-8") == payload


def test_volcengine_coding_plan_catalog_uses_the_documented_endpoint_and_limits() -> None:
    presets = model_presets()
    provider = next(item for item in presets["providers"] if item["id"] == "volcengine")
    assert provider["name"] == "火山引擎 / Volcengine"
    assert [model["id"] for model in provider["models"]] == [
        "deepseek-v4-pro-ga-260813",
        "deepseek-v4-flash-ga-260731",
        "glm-5.3",
        "glm-5.3-flash",
        "minimax-m3",
    ]
    assert all(
        model["url"] == "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"
        for model in provider["models"]
    )

    expected_limits = {
        "deepseek-v4-pro-ga-260813": (1_048_576, 131_072),
        "deepseek-v4-flash-ga-260731": (1_048_576, 393_216),
        "glm-5.3": (1_048_576, 131_072),
        "glm-5.3-flash": (1_048_576, 131_072),
        "minimax-m3": (524_288, 131_072),
    }
    for model_id, (context_window, max_output) in expected_limits.items():
        profile = MODEL_CATALOG.profile(model_id)
        assert profile.context_window_tokens == context_window
        assert profile.max_output_tokens == max_output

    glm = next(model for model in provider["models"] if model["id"] == "glm-5.3")
    glm_flash = next(
        model for model in provider["models"] if model["id"] == "glm-5.3-flash"
    )
    minimax = next(model for model in provider["models"] if model["id"] == "minimax-m3")
    assert glm["supportsImages"] is False
    assert glm_flash["supportsImages"] is True
    assert minimax["supportsImages"] is True


def test_missing_models_file_lists_an_empty_array(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.list() == []
    assert store.path == (tmp_path / "data" / "models.json").resolve()
    assert not store.path.exists()


def test_create_update_delete_round_trip_uses_the_documented_json_array(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    created = store.create(
        provider_id="deepseek",
        model_id="deepseek-flash",
        api_key="sk-deepseek-secret-value",
    )

    assert created.id == "deepseek-flash"
    assert created.name == "DeepSeek-V4.1 Flash"
    assert created.vendor == "DeepSeek"
    assert created.url == "https://api.deepseek.com/chat/completions"
    assert created.supports_tool_call is True
    assert created.supports_images is False
    assert created.supports_reasoning is True
    assert created.reasoning is not None
    assert created.reasoning.default_selection == "high"
    assert created.reasoning.selections == ("none", "low", "high", "max")

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload == [
        {
            "id": "deepseek-flash",
            "name": "DeepSeek-V4.1 Flash",
            "vendor": "DeepSeek",
            "url": "https://api.deepseek.com/chat/completions",
            "apiKey": "sk-deepseek-secret-value",
            "supportsToolCall": True,
            "supportsImages": False,
            "supportsReasoning": True,
            "reasoning": {
                "defaultSelection": "high",
                "selections": ["none", "low", "high", "max"],
            },
        }
    ]
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    unchanged_key = store.update(
        "deepseek-v4-flash",
        provider_id="deepseek",
        model_id="deepseek-v4-flash",
        api_key=None,
    )
    assert unchanged_key.id == "deepseek-flash"
    assert unchanged_key.api_key == "sk-deepseek-secret-value"
    assert store.get("deepseek-v4-flash") == unchanged_key

    deleted = store.delete("deepseek-v4-pro")
    assert deleted.id == "deepseek-flash"
    assert store.list() == []
    assert json.loads(store.path.read_text(encoding="utf-8")) == []


def test_duplicate_or_unknown_model_ids_are_rejected_without_changing_the_file(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.create(
        provider_id="minimax",
        model_id="MiniMax-M3",
        api_key="minimax-secret-value",
    )
    before = store.path.read_bytes()

    with pytest.raises(ModelConfigError, match="already exists"):
        store.create(
            provider_id="minimax",
            model_id="MiniMax-M3",
            api_key="another-secret-value",
        )
    with pytest.raises(ModelConfigError, match="unsupported"):
        store.create(
            provider_id="openai",
            model_id="gpt-5",
            api_key="openai-secret-value",
        )
    with pytest.raises(ModelConfigError, match="API key"):
        store.update(
            "MiniMax-M3",
            provider_id="minimax",
            model_id="MiniMax-M3",
            api_key="bad key",
        )

    assert store.path.read_bytes() == before


def test_api_key_is_an_opaque_nonempty_local_value(tmp_path: Path) -> None:
    store = _store(tmp_path)

    created = store.create(
        provider_id="deepseek",
        model_id="deepseek-flash",
        api_key="sk-xxx",
    )

    assert created.api_key == "sk-xxx"


def test_invalid_existing_file_fails_closed(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    path = data / "models.json"
    path.write_text('{"not":"an array"}', encoding="utf-8")
    path.chmod(0o600)

    store = ModelConfigStore(data)
    with pytest.raises(ModelConfigError, match="invalid"):
        store.initialize()
