from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.domain.input_reference import InputReference


class QueuedMessage(EidosFrozenStrictModel):
    session_id: str
    run_id: str
    user_input: str
    references: tuple[InputReference, ...]


class QueuedMessagePage(EidosFrozenStrictModel):
    items: tuple[QueuedMessage, ...]
    next_sequence: JsonSafeInt | None = None


class QueuedRunUpdated(EidosFrozenStrictModel):
    kind: Literal["updated"]
    session_id: str
    run_id: str
    user_input: str
    updated_at: JsonSafeInt


class QueuedRunDeleted(EidosFrozenStrictModel):
    kind: Literal["deleted"]
    session_id: str
    run_id: str


QueuedRunChange = Annotated[
    QueuedRunUpdated | QueuedRunDeleted, Field(discriminator="kind")
]
