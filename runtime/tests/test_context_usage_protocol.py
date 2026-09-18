from types import SimpleNamespace

from eidos_runtime.application.runs import RunApplication
from eidos_runtime.context.budget import estimate_context_budget
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
    def __init__(
        self,
        input_tokens: int = 185_000,
        *,
        usage_snapshot_id: str = "context-current",
        snapshot_id: str = "context-current",
    ) -> None:
        self.input_tokens = input_tokens
        self.usage_snapshot_id = usage_snapshot_id
        self.snapshot_id = snapshot_id

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

    def latest_model_usage(
        self,
        run_id: str,
        *,
        context_snapshot_id: str | None = None,
    ) -> ModelUsage | None:
        if context_snapshot_id != self.usage_snapshot_id:
            return None
        return ModelUsage(input_tokens=self.input_tokens, output_tokens=1_000)

    def read_latest_context_snapshot(self, run_id: str):
        budget = estimate_context_budget(
            {"messages": [{"content": "persisted estimate"}]},
            context_window_tokens=258_000,
            request_max_output_tokens=8_192,
            message_count=1,
            tool_call_count=0,
            tool_result_count=0,
        )
        return SimpleNamespace(
            snapshot_id=self.snapshot_id,
            plan=SimpleNamespace(token_budget=budget),
        )


def test_context_usage_response_uses_provider_input_for_latest_snapshot() -> None:
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


def test_context_usage_response_ignores_zero_provider_input_tokens() -> None:
    run_id = "00000000-0000-4000-8000-000000000001"
    application = RunApplication(
        store=_Store(input_tokens=0),
        runtime=_Runtime(),
        environment=None,
        scan_text=lambda value: value,
    )

    result = application.context_usage(ContextUsageRequestDto(runId=run_id))

    assert result.context_usage is not None
    assert result.context_usage.source == "estimated"
    assert result.context_usage.active_tokens > 0


def test_context_usage_response_ignores_provider_usage_from_older_snapshot() -> None:
    run_id = "00000000-0000-4000-8000-000000000001"
    application = RunApplication(
        store=_Store(usage_snapshot_id="context-old"),
        runtime=_Runtime(),
        environment=None,
        scan_text=lambda value: value,
    )

    result = application.context_usage(ContextUsageRequestDto(runId=run_id))

    assert result.context_usage is not None
    assert result.context_usage.source == "estimated"
    assert result.context_usage.active_tokens < 185_000


def test_context_usage_response_has_no_value_without_context_snapshot() -> None:
    class _StoreWithoutSnapshot(_Store):
        def read_latest_context_snapshot(self, run_id: str):
            return None

    application = RunApplication(
        store=_StoreWithoutSnapshot(),
        runtime=_Runtime(),
        environment=None,
        scan_text=lambda value: value,
    )

    result = application.context_usage(
        ContextUsageRequestDto(runId="00000000-0000-4000-8000-000000000001")
    )

    assert result.context_usage is None
