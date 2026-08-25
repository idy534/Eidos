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
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "MiniMax-M3",
        "kimi-k3",
        "kimi-k2.7-code-highspeed",
        "deepseek-v4-pro-ga-260813",
        "deepseek-v4-flash-ga-260731",
        "glm-5-2-260617",
        "doubao-seed-evolving",
        "doubao-seed-2-1-pro-260628",
        "doubao-seed-2-1-turbo-260628",
        "doubao-seed-2-0-code-preview-260215",
    ]
    assert all(
        model["url"].endswith("/chat/completions")
        for provider in presets["providers"]
        for model in provider["models"]
    )


def test_volcengine_coding_plan_catalog_uses_the_documented_endpoint_and_limits() -> None:
    presets = model_presets()
    provider = next(item for item in presets["providers"] if item["id"] == "volcengine")
    assert provider["name"] == "火山引擎 / Volcengine"
    assert [model["id"] for model in provider["models"]] == [
        "deepseek-v4-pro-ga-260813",
        "deepseek-v4-flash-ga-260731",
        "glm-5-2-260617",
        "doubao-seed-evolving",
        "doubao-seed-2-1-pro-260628",
        "doubao-seed-2-1-turbo-260628",
        "doubao-seed-2-0-code-preview-260215",
    ]
    assert all(
        model["url"] == "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions"
        for model in provider["models"]
    )

    expected_limits = {
        "deepseek-v4-pro-ga-260813": (1_048_576, 131_072),
        "deepseek-v4-flash-ga-260731": (1_048_576, 393_216),
        "glm-5-2-260617": (1_048_576, 131_072),
        "doubao-seed-evolving": (1_048_576, 262_144),
        "doubao-seed-2-1-pro-260628": (262_144, 262_144),
        "doubao-seed-2-1-turbo-260628": (262_144, 262_144),
        "doubao-seed-2-0-code-preview-260215": (262_144, 131_072),
    }
    for model_id, (context_window, max_output) in expected_limits.items():
        profile = MODEL_CATALOG.profile(model_id)
        assert profile.context_window_tokens == context_window
        assert profile.max_output_tokens == max_output


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
        model_id="deepseek-v4-flash",
        api_key="sk-deepseek-secret-value",
    )

    assert created.id == "deepseek-v4-flash"
    assert created.name == "DeepSeek-V4 Flash"
    assert created.vendor == "DeepSeek"
    assert created.url == "https://api.deepseek.com/chat/completions"
    assert created.supports_tool_call is True
    assert created.supports_images is False
    assert created.supports_reasoning is True
    assert created.reasoning is not None
    assert created.reasoning.default_effort == "high"
    assert created.reasoning.supported_efforts == ("high", "max")

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload == [
        {
            "id": "deepseek-v4-flash",
            "name": "DeepSeek-V4 Flash",
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
    ]
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600

    unchanged_key = store.update(
        "deepseek-v4-flash",
        provider_id="deepseek",
        model_id="deepseek-v4-pro",
        api_key=None,
    )
    assert unchanged_key.id == "deepseek-v4-pro"
    assert unchanged_key.api_key == "sk-deepseek-secret-value"
    assert store.get("deepseek-v4-flash") is None
    assert store.get("deepseek-v4-pro") == unchanged_key

    deleted = store.delete("deepseek-v4-pro")
    assert deleted.id == "deepseek-v4-pro"
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
        model_id="deepseek-v4-flash",
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
