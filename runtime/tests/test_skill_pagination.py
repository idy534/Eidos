from __future__ import annotations

import json
import threading
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from eidos_runtime.extensions.skills import (
    SkillCatalog,
    SkillReadError,
    _SkillReadAdapter,
    _SkillResourceAdapter,
    _skill_content_page,
)
from eidos_runtime.tools.contracts import (
    SkillReadInput,
    SkillReadResourceInput,
    project_tool_result,
)


@pytest.mark.parametrize("is_resource", [False, True])
@pytest.mark.parametrize("unit", ["a", "中🙂", "\x01\n\t\"\\"])
def test_skill_pages_reach_model_without_gaps(is_resource: bool, unit: str) -> None:
    content = unit * (30_000 if unit == "a" else 10_000)
    catalog = Mock(spec=SkillCatalog)
    document = {
        "qualifiedId": "demo:review",
        "resourcePath": "references/rules.md",
        "content": content,
        "contentHash": "a" * 64,
        "source": {"pluginId": "demo", "pluginVersion": "1", "pluginHash": "b" * 64},
    }
    catalog.read_skill.return_value = document
    catalog.read_resource.return_value = document
    adapter = (_SkillResourceAdapter(catalog, {}) if is_resource
               else _SkillReadAdapter(catalog, {}))
    input_model = SkillReadResourceInput if is_resource else SkillReadInput
    arguments = {"qualifiedId": "demo:review"}
    if is_resource:
        arguments["resourcePath"] = "references/rules.md"
    assert input_model.model_validate(arguments).maxBytes == 24_000
    schema = input_model.model_json_schema()["properties"]["maxBytes"]
    assert schema["default"] == schema["maximum"] == 24_000
    with pytest.raises(ValidationError):
        input_model.model_validate({**arguments, "maxBytes": 24_001})

    received = []
    offset = 0
    while True:
        # Omit maxBytes to exercise the adapter's default as well as the DTO.
        result = adapter.execute({**arguments, "offset": offset}, threading.Event())
        assert result["outcome"] == "success"
        projected = project_tool_result(result["toolName"], result).model_result
        data = projected["data"]
        assert data["content"] == result["data"]["content"]
        assert not data.get("truncated", False)
        assert len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= 48 * 1024
        assert data["startByte"] == offset
        assert data["endByte"] == offset + len(data["content"].encode("utf-8"))
        assert data["totalBytes"] == len(content.encode("utf-8"))
        assert 0 < data["endByte"] - offset <= 24_000
        if unit == "a" and offset == 0:
            assert data["endByte"] == min(24_000, len(content))
        received.append(data["content"])
        if data["nextOffset"] is None:
            break
        offset = data["nextOffset"]
    assert "".join(received) == content


def test_skill_page_preserves_smaller_requests_and_rejects_invalid_offsets() -> None:
    page = _skill_content_page("中🙂正文", 0, 4)
    assert page.content == "中"
    assert page.nextOffset == 3
    for offset, max_bytes in ((1, 24_000), (99, 24_000), (0, 24_001), (0, 3)):
        with pytest.raises(SkillReadError, match="skill_resource_invalid_offset"):
            _skill_content_page("中🙂正文", offset, max_bytes)


def test_other_tool_strings_keep_existing_projection_limit() -> None:
    result = {
        "toolName": "read_file",
        "outcome": "success",
        "code": "ok",
        "summary": "Read file",
        "data": {"content": "a" * 24_000},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }
    data = project_tool_result("read_file", result).model_result["data"]
    assert len(data["content"].encode("utf-8")) == 16 * 1024
    assert data["truncated"] is True
