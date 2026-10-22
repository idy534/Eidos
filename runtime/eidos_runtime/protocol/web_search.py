from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from eidos_runtime.domain.web_search import SearchProvider, WebSearchConfig
from eidos_runtime.models import EidosFrozenStrictModel


class WebSearchReadRequest(EidosFrozenStrictModel):
    pass


class SearchCredentialState(EidosFrozenStrictModel):
    deepseek: Literal["missing", "stored", "unavailable"] = "missing"
    langsearch: Literal["missing", "stored", "unavailable"] = "missing"


class SearchCredentialValues(EidosFrozenStrictModel):
    deepseek: str = Field(default="", max_length=1024, repr=False)
    langsearch: str = Field(default="", max_length=1024, repr=False)


class WebSearchResponse(EidosFrozenStrictModel):
    revision: int = Field(ge=0, le=9_007_199_254_740_991)
    config: WebSearchConfig
    credentials: SearchCredentialState
    credential_values: SearchCredentialValues = Field(default_factory=SearchCredentialValues, repr=False)


class SetSearchCredential(EidosFrozenStrictModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    provider: SearchProvider
    action: Literal["set"]
    value: str = Field(min_length=1, max_length=1024, repr=False)

    @model_validator(mode="after")
    def validate_secret(self) -> SetSearchCredential:
        if (
            self.value != self.value.strip()
            or len(self.value.encode()) > 1024
            or any(ord(c) < 33 or ord(c) == 127 for c in self.value)
        ):
            raise ValueError("invalid API Key")
        return self


class ClearSearchCredential(EidosFrozenStrictModel):
    provider: SearchProvider
    action: Literal["clear"]


class WebSearchSaveRequest(EidosFrozenStrictModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    expected_revision: int = Field(ge=0, le=9_007_199_254_740_991)
    config: WebSearchConfig
    credential_changes: list[
        Annotated[
            SetSearchCredential | ClearSearchCredential, Field(discriminator="action")
        ]
    ] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def unique_providers(self) -> WebSearchSaveRequest:
        if len({change.provider for change in self.credential_changes}) != len(
            self.credential_changes
        ):
            raise ValueError("duplicate credential change")
        return self


class WebSearchOfficialPageRequest(EidosFrozenStrictModel):
    provider: SearchProvider
    target: Literal["keys", "docs", "pricing"]
