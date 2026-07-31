from __future__ import annotations

import pytest
from pydantic import ValidationError

from eidos_runtime.models import EidosModel, JsonSafeInt


class SafeIntegerValue(EidosModel):
    value: JsonSafeInt


@pytest.mark.parametrize(
    "value",
    [0, 1, -1, 9_007_199_254_740_991, -9_007_199_254_740_991],
)
def test_json_safe_integer_accepts_javascript_safe_integers(value: int) -> None:
    assert SafeIntegerValue(value=value).value == value


@pytest.mark.parametrize(
    "value",
    [
        9_007_199_254_740_992,
        -9_007_199_254_740_992,
        True,
        False,
        1.0,
        "1",
    ],
)
def test_json_safe_integer_rejects_unsafe_or_coerced_values(value: object) -> None:
    with pytest.raises(ValidationError):
        SafeIntegerValue(value=value)
