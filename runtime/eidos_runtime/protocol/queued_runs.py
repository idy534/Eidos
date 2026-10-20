from __future__ import annotations

from pydantic import Field, StrictInt, StrictStr
from pydantic.alias_generators import to_camel

from eidos_runtime.domain.queued_runs import QueuedMessage, QueuedRunChange
from eidos_runtime.protocol.methods import _OperationRequest, _CanonicalIdRequest, MethodResultDto

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class QueuedRunDeleteRequest(_OperationRequest):
    model_config = {"alias_generator": to_camel}
    session_id: StrictStr = Field(pattern=UUID_PATTERN)
    run_id: StrictStr = Field(pattern=UUID_PATTERN)
    operation_id: StrictStr | None = Field(default=None, pattern=UUID_PATTERN)
    expected_user_input: StrictStr = Field(max_length=64 * 1024)
    _canonical_id_fields = ("operation_id", "session_id", "run_id")


class QueuedRunUpdateRequest(QueuedRunDeleteRequest):
    user_input: StrictStr = Field(max_length=64 * 1024)


class QueuedRunSteerRequest(QueuedRunDeleteRequest):
    target_run_id: StrictStr = Field(pattern=UUID_PATTERN)
    _canonical_id_fields = (*QueuedRunDeleteRequest._canonical_id_fields, "target_run_id")


class QueuedRunChangeResponse(MethodResultDto):
    change: QueuedRunChange


class QueuedRunListRequest(_CanonicalIdRequest):
    model_config = {"alias_generator": to_camel}
    session_id: StrictStr = Field(pattern=UUID_PATTERN)
    after_sequence: StrictInt = Field(default=0, ge=0, le=9_007_199_254_740_991)
    _canonical_id_fields = ("session_id",)


class QueuedRunListResponse(MethodResultDto):
    model_config = {"alias_generator": to_camel}
    items: list[QueuedMessage] = Field(max_length=100)
    next_sequence: StrictInt | None = Field(default=None, ge=1, le=9_007_199_254_740_991)
