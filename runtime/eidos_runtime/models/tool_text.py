from pydantic import Field, StrictInt, StrictStr

from eidos_runtime.models.base import EidosFrozenStrictModel


class ToolTextPage(EidosFrozenStrictModel):
    content: StrictStr
    next_offset: StrictInt = Field(ge=0, le=9_007_199_254_740_991)
    total_characters: StrictInt = Field(ge=0, le=9_007_199_254_740_991)
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
