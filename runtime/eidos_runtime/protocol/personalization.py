from __future__ import annotations

from pydantic import ConfigDict, Field

from eidos_runtime.domain.personalization import PersonalizationPreferences
from eidos_runtime.models import EidosFrozenStrictModel


class PersonalizationReadRequest(EidosFrozenStrictModel):
    pass


class PersonalizationSaveRequest(EidosFrozenStrictModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    expected_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    preferences: PersonalizationPreferences

class PersonalizationResponse(EidosFrozenStrictModel):
    revision: int = Field(ge=0, le=9_007_199_254_740_991)
    preferences: PersonalizationPreferences
