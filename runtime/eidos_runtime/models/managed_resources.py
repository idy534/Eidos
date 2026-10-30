"""Trusted release descriptions and Desktop projections for managed resources."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.models.runtime_dependencies import RuntimeDependencyManifest


ResourcePhase = Literal["missing", "checking", "downloading", "verifying", "extracting", "ready", "failed"]
RESOURCE_ID = r"^[a-z][a-z0-9-]{0,63}$"


def resource_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or "\\" in value or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("resource paths must be normalized relative POSIX paths")
    return value


class ResourcePack(EidosFrozenStrictModel):
    id: str = Field(pattern=RESOURCE_ID)
    title: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")
    location: str = Field(max_length=512)
    url: str = Field(max_length=2048)
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    archive_bytes: int = Field(gt=0, le=2 * 1024**3)
    expanded_bytes: int = Field(gt=0, le=3 * 1024**3)
    requires: tuple[str, ...] = Field(default=(), max_length=16)
    font_directory: str | None = Field(default=None, max_length=512)
    python_abi: Literal["cp312"] | None = None
    manifest: RuntimeDependencyManifest

    _location = field_validator("location")(resource_relative_path)
    _fonts = field_validator("font_directory")(
        lambda value: None if value is None else resource_relative_path(value)
    )

    @field_validator("url")
    @classmethod
    def trusted_download_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname != "github.com"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.path.startswith("/idy534/Eidos/releases/download/")):
            raise ValueError("resource URL must identify an Eidos GitHub release asset")
        return value


class ResourceIndex(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    version: str = Field(min_length=1, max_length=64)
    target: Literal["darwin-arm64"] = "darwin-arm64"
    packs: tuple[ResourcePack, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def unique_packs(self) -> ResourceIndex:
        ids = [pack.id for pack in self.packs]
        locations = [pack.location for pack in self.packs]
        if len(set(ids)) != len(ids) or len(set(locations)) != len(locations):
            raise ValueError("resource ids and locations must be unique")
        for pack in self.packs:
            if any(dep not in ids or dep == pack.id for dep in pack.requires):
                raise ValueError("resource prerequisites must identify another pack")
            for other in self.packs:
                if other.id != pack.id and PurePosixPath(pack.location).is_relative_to(other.location):
                    raise ValueError("resource locations must not overlap")
        # The standard library owns dependency cycle detection.
        from graphlib import TopologicalSorter
        tuple(TopologicalSorter({p.id: p.requires for p in self.packs}).static_order())
        return self


class SkillResourceProfiles(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    profiles: dict[str, tuple[str, ...]] = Field(max_length=16)

    @field_validator("profiles", mode="before")
    @classmethod
    def yaml_profiles(cls, value: object) -> object:
        if isinstance(value, dict):
            return {key: tuple(items) if isinstance(items, list) else items for key, items in value.items()}
        return value

    @model_validator(mode="after")
    def bounded_profiles(self) -> SkillResourceProfiles:
        import re
        if "python" not in self.profiles:
            raise ValueError("resource profiles require a default python entry")
        for profile, packs in self.profiles.items():
            if not re.fullmatch(r"[a-z][a-z0-9+-]{0,63}", profile) or not 1 <= len(packs) <= 16:
                raise ValueError("invalid resource profile")
            if len(set(packs)) != len(packs) or any(not re.fullmatch(RESOURCE_ID, pack) for pack in packs):
                raise ValueError("invalid resource pack list")
        return self


class ResourceProgress(EidosFrozenStrictModel):
    id: str = Field(pattern=RESOURCE_ID)
    title: str
    version: str
    phase: ResourcePhase
    downloaded_bytes: int = Field(default=0, ge=0, le=2 * 1024**3)
    total_bytes: int = Field(default=0, ge=0, le=2 * 1024**3)
    error_code: str | None = None


class ResourceStatus(EidosFrozenStrictModel):
    available: bool
    directory: str
    packs: tuple[ResourceProgress, ...] = ()


class ResourceStatusRequest(EidosFrozenStrictModel):
    pass


class ResourceRetryRequest(EidosFrozenStrictModel):
    pack_id: str = Field(pattern=RESOURCE_ID)


class ResourceRetryResponse(EidosFrozenStrictModel):
    accepted: bool
