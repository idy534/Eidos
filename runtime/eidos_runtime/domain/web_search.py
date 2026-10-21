from __future__ import annotations

from datetime import date
import ipaddress
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel

SearchProvider = Literal["deepseek", "langsearch"]


class WebSearchError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SearchOptions(EidosFrozenStrictModel):
    default_count: int = Field(default=5, ge=1, le=50)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    include_page_content: bool = False
    max_excerpt_characters: int = Field(default=3000, ge=100, le=10000)


class DeepSeekSearchOptions(SearchOptions):
    localization: str | None = Field(default=None, max_length=45)

    @field_validator("localization")
    @classmethod
    def validate_ip(cls, value: str | None) -> str | None:
        if value is not None:
            return str(ipaddress.ip_address(value))
        return value


class LangSearchOptions(SearchOptions):
    freshness: str = Field(default="noLimit", max_length=22)
    include_domains: list[str] = Field(default_factory=list, max_length=20)
    exclude_domains: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("freshness")
    @classmethod
    def validate_freshness(cls, value: str) -> str:
        if value in {"noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"}:
            return value
        dates = value.split("..")
        if len(dates) not in {1, 2} or any(
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item) for item in dates
        ):
            raise ValueError("invalid freshness")
        parsed = [date.fromisoformat(item) for item in dates]
        if parsed[0] > parsed[-1]:
            raise ValueError("invalid date range")
        return value

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def validate_domains(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            domain = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
            if (
                len(domain) > 253
                or "." not in domain
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in domain.split(".")
                )
            ):
                raise ValueError("invalid domain")
            if domain not in result:
                result.append(domain)
        return result

    @model_validator(mode="after")
    def validate_domain_sets(self) -> LangSearchOptions:
        if set(self.include_domains) & set(self.exclude_domains):
            raise ValueError("domain lists overlap")
        return self


class WebSearchConfig(EidosFrozenStrictModel):
    default_provider: SearchProvider | None = None
    deepseek: DeepSeekSearchOptions = Field(default_factory=DeepSeekSearchOptions)
    langsearch: LangSearchOptions = Field(default_factory=LangSearchOptions)

    def options(
        self, provider: SearchProvider
    ) -> DeepSeekSearchOptions | LangSearchOptions:
        return self.deepseek if provider == "deepseek" else self.langsearch


class SearchCredentialRefs(EidosFrozenStrictModel):
    deepseek: str | None = None
    langsearch: str | None = None

    def get(self, provider: SearchProvider) -> str | None:
        return self.deepseek if provider == "deepseek" else self.langsearch


class StoredWebSearchSettings(EidosFrozenStrictModel):
    revision: int = Field(default=0, ge=0, le=9_007_199_254_740_991)
    config: WebSearchConfig = Field(default_factory=WebSearchConfig)
    credentials: SearchCredentialRefs = Field(default_factory=SearchCredentialRefs)


class RunWebSearchSnapshot(EidosFrozenStrictModel):
    revision: int = Field(ge=0, le=9_007_199_254_740_991)
    provider: SearchProvider
    config: WebSearchConfig
    credential_ref: str
