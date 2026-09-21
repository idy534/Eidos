from typing import Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from eidos_runtime.domain.input_reference import InputReference, InputReferenceId
from eidos_runtime.protocol.schemas import ClosedModel


class InputPrepareRequest(ClosedModel):
    session_id: StrictStr | None = Field(default=None, alias="sessionId", max_length=128)
    origin: Literal["selected", "clipboard"] = "selected"
    kind: Literal['file', 'directory', 'image', 'skill', 'mcp', 'plugin', 'history', 'excerpt']
    source: StrictStr = Field(min_length=1, max_length=4096)
    label: StrictStr | None = Field(default=None, max_length=512)
    text: StrictStr | None = Field(default=None, max_length=65536)
    item_ids: list[StrictStr] = Field(default_factory=list, alias='itemIds', max_length=20)
    plugin_id: StrictStr | None = Field(default=None, alias='pluginId', max_length=256)
    start_line: StrictInt | None = Field(default=None, alias='startLine', ge=1)
    end_line: StrictInt | None = Field(default=None, alias='endLine', ge=1)

    @model_validator(mode='after')
    def line_range(self):
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError('both line bounds are required')
        if self.start_line is not None and self.end_line < self.start_line:
            raise ValueError('invalid line range')
        if self.text is not None and self.kind != 'excerpt':
            raise ValueError('only excerpts accept text')
        return self


class InputReadRequest(ClosedModel):
    id: InputReferenceId


class InputReferenceResponse(ClosedModel):
    reference: InputReference


class InputPreviewResponse(ClosedModel):
    reference: InputReference
    text: StrictStr
    thumbnail: StrictStr | None = None


class DraftReadRequest(ClosedModel):
    key: StrictStr = Field(min_length=1, max_length=128)


class DraftWriteRequest(DraftReadRequest):
    text: StrictStr = Field(max_length=65536)
    references: list[InputReferenceId] = Field(default_factory=list, max_length=20)


class DraftResponse(ClosedModel):
    def to_json_value(self):
        return self.model_dump(mode='json', by_alias=True, exclude_none=True)

    text: StrictStr
    references: list[InputReference]
