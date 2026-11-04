from pydantic import Field, StrictInt, StrictStr

from eidos_runtime.models.base import EidosFrozenStrictModel


class ToolTextPage(EidosFrozenStrictModel):
    content: StrictStr
    next_offset: StrictInt = Field(ge=0, le=9_007_199_254_740_991)
    total_characters: StrictInt = Field(ge=0, le=9_007_199_254_740_991)
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


TOOL_CONTENT_CHUNK_BYTES = 512 * 1024


class ToolContentChunk(EidosFrozenStrictModel):
    """One bounded slice of a saved tool image; base64 keeps each reply under 1 MiB."""

    data: StrictStr
    mime_type: StrictStr = Field(pattern=r"^image/(png|jpeg)$")
    size_bytes: StrictInt = Field(ge=1, le=10 * 1024 * 1024)
    next_offset: StrictInt = Field(ge=0, le=10 * 1024 * 1024)
    complete: bool
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
