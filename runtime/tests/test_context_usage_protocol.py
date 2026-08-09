from eidos_runtime.application.runs import RunApplication
from eidos_runtime.model.client import ModelProfileSnapshot, ModelUsage
from eidos_runtime.protocol.methods import (
    ContextUsageRequestDto,
    ContextUsageResponseDto,
)


class _Runtime:
    def prepare_next(self):
        return None

    def release(self, start):
        return None

    def abort(self, start):
        return None

    def cancel_run(self, run_id: str, *, operation_id: str | None = None):
        return {}


class _Store:
    def read_model_profile(self, run_id: str) -> ModelProfileSnapshot:
        return ModelProfileSnapshot(
            provider_id="fixture",
            model_id="fixture-model",
            context_window_tokens=258_000,
            max_output_tokens=8_192,
            request_timeout_seconds=120,
            supports_tools=True,
            supports_json_schema_output=False,
            supports_reasoning=False,
        )

    def latest_model_usage(self, run_id: str) -> ModelUsage | None:
        return ModelUsage(input_tokens=185_000, output_tokens=1_000)

    def read_latest_context_snapshot(self, run_id: str):
        return None


def test_context_usage_response_uses_latest_provider_input_tokens() -> None:
    run_id = "00000000-0000-4000-8000-000000000001"
    application = RunApplication(
        store=_Store(), runtime=_Runtime(), environment=None, scan_text=lambda value: value,
    )

    result = application.context_usage(ContextUsageRequestDto(runId=run_id))

    assert isinstance(result, ContextUsageResponseDto)
    assert result.context_usage is not None
    assert result.context_usage.active_tokens == 185_000
    assert result.context_usage.context_window_tokens == 258_000
    assert result.context_usage.source == "provider"
    assert result.context_usage.percent_used == 71.7
