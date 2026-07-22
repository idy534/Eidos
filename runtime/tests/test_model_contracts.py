from __future__ import annotations

from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model.client import (  # noqa: E402
    ModelProfileSnapshot,
    ModelRequestFailure,
    ModelResponse,
    ModelToolDefinition,
    ModelUsage,
)
from eidos_runtime.model.prompts import SYSTEM_PROMPT, TITLE_PROMPT  # noqa: E402


class ModelContractTests(unittest.TestCase):
    def test_contracts_are_strict_frozen_and_closed(self) -> None:
        tool = ModelToolDefinition(
            name="read_file",
            description="Read a file.",
            parameters_json_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
        with self.assertRaises(ValidationError):
            tool.name = "changed"  # type: ignore[misc]
        with self.assertRaises(ValidationError):
            ModelToolDefinition.model_validate({
                **tool.model_dump(),
                "unexpected": True,
            })

    def test_response_and_failure_defaults_are_provider_neutral(self) -> None:
        self.assertEqual(ModelResponse(), ModelResponse(text="", tool_calls=()))
        self.assertEqual(ModelUsage().model_dump(), {
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
            "details": {},
        })
        failure = ModelRequestFailure(code="provider_timeout", retryable=True)
        self.assertIsNone(failure.status_code)
        self.assertNotIn("body", ModelRequestFailure.model_fields)

    def test_profile_snapshot_is_closed_and_versioned(self) -> None:
        profile = ModelProfileSnapshot(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            context_window_tokens=802_816,
            max_output_tokens=8_192,
            request_timeout_seconds=120.0,
            supports_tools=True,
            supports_json_schema_output=False,
            supports_reasoning=True,
        )
        self.assertEqual(profile.schema_version, 1)
        self.assertEqual(profile.wire_api, "chat_completions")
        self.assertEqual(profile.pydantic_ai_version, "2.13.0")

    def test_prompts_are_provider_neutral_model_resources(self) -> None:
        self.assertIn("Eidos", SYSTEM_PROMPT)
        self.assertIn("task title", TITLE_PROMPT)


if __name__ == "__main__":
    unittest.main()
