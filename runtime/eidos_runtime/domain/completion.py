from typing import Annotated, Literal

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CompletionText = Annotated[str, Field(min_length=1, max_length=800)]
OutputPath = Annotated[str, Field(min_length=1, max_length=512)]


class CompletionAssessment(EidosFrozenStrictModel):
    """A model judgement, never permission or proof of file quality."""

    state: Literal["complete", "continue", "blocked"]
    explanation: CompletionText
    remaining_work: tuple[CompletionText, ...] = Field(default=(), max_length=16)
    requested_outputs: tuple[OutputPath, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def coherent_completion(self):
        if self.state == "complete" and self.remaining_work:
            raise ValueError("completed_with_remaining_work")
        if self.state == "continue" and not self.remaining_work:
            raise ValueError("continuation_requires_remaining_work")
        return self


class CompletionCheckRecord(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    fingerprint: Sha256
    candidate_item_id: str = Field(min_length=1, max_length=128)
    candidate_sha256: Sha256
    model_attempt_id: str = Field(min_length=1, max_length=128)
    status: Literal["running", "complete", "continue", "blocked", "failed"]
    reason: str = Field(min_length=1, max_length=128)
    explanation: CompletionText | None = None
    remaining_work: tuple[CompletionText, ...] = Field(default=(), max_length=16)
    requested_outputs: tuple[OutputPath, ...] = Field(default=(), max_length=20)


class CompletionOutput(EidosFrozenStrictModel):
    path: OutputPath
    version: Sha256


class CompletionFacts(EidosFrozenStrictModel):
    workspace_version: JsonSafeInt
    reconciliation_epoch: JsonSafeInt
    last_tool_event_id: JsonSafeInt
    last_user_sequence: JsonSafeInt
    has_tools: bool
    pending_input: bool
    outputs: tuple[CompletionOutput, ...] = Field(default=(), max_length=640)


class CompletionOutcome(EidosFrozenStrictModel):
    action: Literal["accept", "continue", "stop"]
    reason: str = Field(min_length=1, max_length=128)
