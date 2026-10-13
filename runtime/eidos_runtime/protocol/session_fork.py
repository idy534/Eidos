from __future__ import annotations

from typing import Annotated

from pydantic import Field
from pydantic.alias_generators import to_camel

from eidos_runtime.domain.session_fork import (
    NewWorktreeForkTarget,
    SameWorkspaceForkTarget,
)
from eidos_runtime.protocol.methods import MethodResultDto, _OperationRequest
from eidos_runtime.protocol.schemas import SessionDto


class SessionForkRequest(_OperationRequest):
    model_config = {"alias_generator": to_camel}
    operation_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    source_session_id: str = Field(min_length=1, max_length=256)
    source_item_id: str = Field(min_length=1, max_length=256)
    target: Annotated[
        SameWorkspaceForkTarget | NewWorktreeForkTarget, Field(discriminator="kind")
    ]


class SessionForkResponse(MethodResultDto):
    session: SessionDto
