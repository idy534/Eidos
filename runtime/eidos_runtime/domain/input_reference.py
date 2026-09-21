"""Immutable user-selected input, separate from tool permissions and instructions."""
from typing import Annotated, Literal

from pydantic import Field, StrictStr

from eidos_runtime.models import EidosFrozenStrictModel


InputReferenceId = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class InputReference(EidosFrozenStrictModel):
    id: InputReferenceId
    kind: Literal['file', 'directory', 'image', 'skill', 'mcp', 'plugin', 'history', 'excerpt']
    label: str = Field(min_length=1, max_length=512)
    source: str = Field(max_length=4096)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: Literal['content', 'location', 'selection']
    size: int = Field(ge=0, le=10 * 1024 * 1024)


class InputSnapshot(EidosFrozenStrictModel):
    reference: InputReference
    text: str = Field(default='', max_length=65536)
    image: str | None = Field(default=None, max_length=14 * 1024 * 1024)
    mime: Literal['image/png', 'image/jpeg'] | None = None
    image_token_estimate: int = Field(default=0, ge=0, le=1000000)
    selection_id: str | None = Field(default=None, max_length=512)
    plugin_id: str | None = Field(default=None, max_length=256)


class InputDraft(EidosFrozenStrictModel):
    text: str = Field(default='', max_length=65536)
    references: tuple[InputReference, ...] = Field(default=(), max_length=20)
