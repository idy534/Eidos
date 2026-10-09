from typing import Annotated, Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CompletionText = Annotated[str, Field(min_length=1, max_length=800)]
OutputPath = Annotated[str, Field(min_length=1, max_length=512)]


class CompletionCheckRecord(EidosFrozenStrictModel):
    """Decode historical completion-review events without replaying assessments."""
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
