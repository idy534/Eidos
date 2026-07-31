from __future__ import annotations

from typing import Annotated

from pydantic import Field, StrictInt


JSON_SAFE_INTEGER_MAX = 9_007_199_254_740_991
JSON_SAFE_INTEGER_MIN = -JSON_SAFE_INTEGER_MAX

JsonSafeInt = Annotated[
    StrictInt,
    Field(ge=JSON_SAFE_INTEGER_MIN, le=JSON_SAFE_INTEGER_MAX),
]
