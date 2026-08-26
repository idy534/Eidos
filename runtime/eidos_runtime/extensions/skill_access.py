"""Run-scoped access state for resources belonging to catalog Skills."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import os
from pathlib import Path
import stat
import threading
from types import MappingProxyType
from typing import Mapping
import urllib.parse

from eidos_runtime.extensions.skill_invocation import (
    parse_skill_script_invocation,
)


MAX_SKILL_DOCUMENT_BYTES = 128 * 1024


class SkillActivationKind(StrEnum):
    EXPLICIT = "explicit"
    MODEL_READ = "model_read"
    IMPLICIT = "implicit"


@dataclass(frozen=True, slots=True)
class SkillAccessRecord:
    qualified_id: str
    canonical_root: Path
    source: str
    provenance: Mapping[str, str]
    content_hash: str
    activation_kind: SkillActivationKind
    script_path: Path | None = None
    source_kind: str = "user"

    def result_data(self) -> dict[str, object]:
        metadata = {
            "skillQualifiedId": self.qualified_id,
            "invocationType": self.activation_kind.value,
            "source": self.source,
            "provenance": dict(self.provenance),
        }
        return {
            # Keep the legacy flat fields while adding one bounded canonical
            # object for consumers that need the complete invocation fact.
            "qualifiedId": self.qualified_id,
            "invocationType": self.activation_kind.value,
            "source": self.source,
            "provenance": dict(self.provenance),
            "skillQualifiedId": self.qualified_id,
            "skillInvocation": metadata,
        }


class SkillAccessError(ValueError):
    """Raised when a catalog Skill cannot be activated safely."""


class SkillAccess:
    """Thread-safe active Skill state for one immutable catalog snapshot.

    A root can only be derived from an entry's ``main_resource_locator``.  No
    public method accepts a caller-provided root.  The root and the Skill
    document are revalidated on activation so a changed snapshot resource
    fails closed before it can reach the Shell policy compiler.
    """

    def __init__(self, entries: tuple[object, ...]) -> None:
        ordered = tuple(sorted(
            entries,
            key=lambda entry: str(getattr(entry, "qualified_id")).encode("utf-8"),
        ))
        by_id: dict[str, object] = {}
        for entry in ordered:
            qualified_id = str(getattr(entry, "qualified_id"))
            if qualified_id in by_id:
                raise SkillAccessError("duplicate Skill qualified id")
            by_id[qualified_id] = entry
        self._entries = MappingProxyType(by_id)
        self._records: dict[str, SkillAccessRecord] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_snapshot(
        cls,
        snapshot: object,
    ) -> SkillAccess:
        entries = getattr(snapshot, "entries", None)
        if not isinstance(entries, tuple):
            raise SkillAccessError("Skill catalog snapshot is invalid")
        canonical_hash = getattr(snapshot, "canonical_hash", None)
        catalog_hash = getattr(snapshot, "catalog_hash", None)
        if callable(canonical_hash) and isinstance(catalog_hash, str):
            empty_snapshot_placeholder = (
                not entries and catalog_hash == "0" * 64
            )
            if catalog_hash != canonical_hash() and not empty_snapshot_placeholder:
                raise SkillAccessError("Skill catalog snapshot hash is invalid")
        return cls(entries)

    def activate_explicit(self, qualified_id: str) -> SkillAccessRecord:
        return self.activate(qualified_id, SkillActivationKind.EXPLICIT)

    def activate_model_read(self, qualified_id: str) -> SkillAccessRecord:
        return self.activate(qualified_id, SkillActivationKind.MODEL_READ)

    # Callback-friendly name for the future skill_read integration.
    activate_skill_read = activate_model_read

    def activate(
        self,
        qualified_id: str,
        activation_kind: SkillActivationKind,
    ) -> SkillAccessRecord:
        if not isinstance(activation_kind, SkillActivationKind):
            raise SkillAccessError("unknown Skill activation kind")
        with self._lock:
            entry = self._entries.get(qualified_id)
            if entry is None:
                raise SkillAccessError("skill is not in the catalog")
            root, content_hash = _trusted_skill_root(entry)
            current = self._records.get(qualified_id)
            selected_kind = _stronger_activation(
                current.activation_kind if current is not None else None,
                activation_kind,
            )
            record = _record_for_entry(
                entry,
                root,
                content_hash,
                selected_kind,
            )
            self._records[qualified_id] = record
            return record

    def activate_implicit(
        self,
        command: str,
        cwd: Path,
    ) -> SkillAccessRecord | None:
        invocation = parse_skill_script_invocation(command, cwd)
        if invocation is None:
            return None
        candidate = _verified_script_path(invocation.script_path)
        if candidate is None:
            return None
        with self._lock:
            matches: list[tuple[str, object, Path, str]] = []
            for qualified_id, entry in self._entries.items():
                if getattr(entry, "allow_implicit_invocation", None) is False:
                    continue
                try:
                    root, content_hash = _trusted_skill_root(entry)
                except SkillAccessError:
                    continue
                scripts = root / "scripts"
                try:
                    scripts_metadata = scripts.lstat()
                    if (
                        stat.S_ISLNK(scripts_metadata.st_mode)
                        or not stat.S_ISDIR(scripts_metadata.st_mode)
                        or scripts_metadata.st_uid != os.getuid()
                    ):
                        continue
                    scripts = scripts.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if scripts == candidate or scripts not in candidate.parents:
                    continue
                matches.append((qualified_id, entry, root, content_hash))
            if not matches:
                return None
            if len(matches) > 1:
                matches.sort(key=lambda value: value[0].encode("utf-8"))
            qualified_id, entry, root, content_hash = matches[0]
            current = self._records.get(qualified_id)
            selected_kind = _stronger_activation(
                current.activation_kind if current is not None else None,
                SkillActivationKind.IMPLICIT,
            )
            record = _record_for_entry(
                entry,
                root,
                content_hash,
                selected_kind,
                script_path=candidate,
            )
            self._records[qualified_id] = record
            return record

    def active_roots(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(
                record.canonical_root
                for record in sorted(
                    self._records.values(),
                    key=lambda value: value.qualified_id.encode("utf-8"),
                )
            )

    def records(self) -> tuple[SkillAccessRecord, ...]:
        with self._lock:
            return tuple(sorted(
                self._records.values(),
                key=lambda value: value.qualified_id.encode("utf-8"),
            ))

    def result_data(self, qualified_id: str) -> dict[str, object] | None:
        with self._lock:
            record = self._records.get(qualified_id)
            return record.result_data() if record is not None else None


# Concise aliases for callers that use "activation" as the domain term.
SkillActivation = SkillAccessRecord


def _stronger_activation(
    current: SkillActivationKind | None,
    requested: SkillActivationKind,
) -> SkillActivationKind:
    if current is None:
        return requested
    ranking = {
        SkillActivationKind.IMPLICIT: 0,
        SkillActivationKind.MODEL_READ: 1,
        SkillActivationKind.EXPLICIT: 2,
    }
    return current if ranking[current] >= ranking[requested] else requested


def _record_for_entry(
    entry: object,
    root: Path,
    content_hash: str,
    activation_kind: SkillActivationKind,
    *,
    script_path: Path | None = None,
) -> SkillAccessRecord:
    source = str(getattr(entry, "source_identity"))
    source_kind = str(getattr(entry, "source_kind", "user"))
    provenance = MappingProxyType({
        "version": str(getattr(entry, "source_version")),
        "hash": str(getattr(entry, "source_hash")),
        "locator": str(getattr(entry, "main_resource_locator")),
        "sourceKind": source_kind,
    })
    return SkillAccessRecord(
        qualified_id=str(getattr(entry, "qualified_id")),
        canonical_root=root,
        source=source,
        provenance=provenance,
        content_hash=content_hash,
        activation_kind=activation_kind,
        script_path=script_path,
        source_kind=source_kind,
    )


def _trusted_skill_root(entry: object) -> tuple[Path, str]:
    locator = str(getattr(entry, "main_resource_locator", ""))
    try:
        document = _locator_path(locator)
    except (TypeError, ValueError):
        document = None
    if document is None:
        raise SkillAccessError("skill has no trusted filesystem root")
    try:
        metadata = document.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size > MAX_SKILL_DOCUMENT_BYTES
        ):
            raise SkillAccessError("skill has no trusted filesystem root")
        root = document.parent.resolve(strict=True)
        root_metadata = root.lstat()
        if (
            stat.S_ISLNK(root_metadata.st_mode)
            or not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != os.getuid()
        ):
            raise SkillAccessError("skill has no trusted filesystem root")
        data = document.read_bytes()
        text = data.decode("utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError):
        raise SkillAccessError("skill has no trusted filesystem root") from None
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if content_hash != str(getattr(entry, "content_hash", "")):
        raise SkillAccessError("skill content hash changed")
    return root, content_hash


def _locator_path(locator: str) -> Path | None:
    if not locator:
        return None
    parsed = urllib.parse.urlparse(locator)
    if parsed.scheme:
        if parsed.scheme != "file" or parsed.netloc:
            return None
        raw_path = urllib.parse.unquote(parsed.path)
        path = Path(raw_path)
    else:
        path = Path(locator)
    if not path.is_absolute() or path.name != "SKILL.md":
        return None
    return path


def _verified_script_path(path: Path) -> Path | None:
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            return None
        return path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
