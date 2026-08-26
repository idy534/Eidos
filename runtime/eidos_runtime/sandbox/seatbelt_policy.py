from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eidos_runtime.sandbox.permissions import (
    EffectivePermissionProfile,
    FileSystemAccessMode,
    MaterializedFileSystemPermissionEntry,
)


BASE_POLICY_PATH = Path(__file__).with_name("seatbelt.sbpl")


@dataclass(frozen=True)
class CompiledSeatbeltPolicy:
    policy: str
    parameters: dict[str, str]


class SeatbeltPolicyCompiler:
    def __init__(self, base_policy_path: Path = BASE_POLICY_PATH) -> None:
        self.base_policy_path = base_policy_path

    def compile(
        self, profile: EffectivePermissionProfile
    ) -> CompiledSeatbeltPolicy:
        try:
            base = self.base_policy_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError("seatbelt base policy is unavailable") from error
        sections = [base.rstrip()]
        parameters: dict[str, str] = {}
        for index, entry in enumerate(
            item for item in profile.entries if item.source == "additional"
        ):
            key = f"ADDITIONAL_{entry.access.value.upper()}_{index}"
            parameters[key] = entry.resolved_path
            sections.append(_metadata_rule(key))
            sections.append(_allow_rule(entry, key))
        skill_root_keys: tuple[str, ...] = ()
        for index, root in enumerate(profile.active_skill_roots):
            key = f"SKILL_ROOT_{index}"
            parameters[key] = root
            skill_root_keys += (key,)
            sections.append(_metadata_rule(key))
            sections.append(
                "(allow file-read* file-test-existence "
                f'(subpath (param "{key}")))\n'
                f'(allow file-map-executable (subpath (param "{key}")))\n'
                f'(deny file-write* (subpath (param "{key}")))'
            )
        workspace_exceptions: dict[str, str] = {}
        for index, workspace_root in enumerate(profile.workspace_roots):
            workspace = Path(workspace_root).resolve(strict=False)
            for entry in (
                *profile.permanent_denies,
                *profile.hard_confidentiality_denies,
            ):
                protected = Path(entry.resolved_path)
                if protected != workspace and protected in workspace.parents:
                    key = f"WORKSPACE_ROOT_{index}"
                    parameters[key] = str(workspace)
                    workspace_exceptions[entry.resolved_path] = key
        for index, entry in enumerate(
            (*profile.permanent_denies, *profile.hard_confidentiality_denies)
        ):
            key = f"PERMANENT_DENY_{index}"
            parameters[key] = entry.resolved_path
            sections.append(
                _deny_rule(
                    entry,
                    key,
                    exception_key=workspace_exceptions.get(entry.resolved_path),
                    exception_keys=(
                        tuple(
                            key
                            for key, root in zip(
                                skill_root_keys, profile.active_skill_roots
                            )
                            if Path(root) != Path(entry.resolved_path)
                            and Path(root).is_relative_to(entry.resolved_path)
                        )
                        if entry.source == "permanent_deny"
                        else ()
                    ),
                )
            )
        for index, path in enumerate(profile.protected_write_paths):
            key = f"PROTECTED_WRITE_{index}"
            parameters[key] = path
            sections.append(
                f'(deny file-write* (subpath (param "{key}")))'
            )
        for index, path in enumerate(profile.runtime_roots):
            key = f"RUNTIME_ROOT_{index}"
            parameters[key] = path
            sections.append(
                f'{_metadata_rule(key)}\n'
                f'(allow file-read* file-test-existence '
                f'(subpath (param "{key}")))\n'
                f'(allow file-map-executable (subpath (param "{key}")))'
            )
        if profile.network_enabled:
            sections.append(
                "(allow network-outbound)\n"
                "(allow network-inbound)\n"
                "(allow network-bind)"
            )
        return CompiledSeatbeltPolicy(
            "\n\n".join(section for section in sections if section),
            parameters,
        )


def _filter(entry: MaterializedFileSystemPermissionEntry, key: str) -> str:
    operation = "subpath" if entry.recursive else "literal"
    return f'({operation} (param "{key}"))'


def _metadata_rule(key: str) -> str:
    return (
        "(allow file-read-metadata file-test-existence "
        f'(path-ancestors (param "{key}")))'
    )


def _allow_rule(
    entry: MaterializedFileSystemPermissionEntry, key: str
) -> str:
    path_filter = _filter(entry, key)
    if entry.access is FileSystemAccessMode.READ:
        return f"(allow file-read* file-test-existence {path_filter})"
    if entry.access is FileSystemAccessMode.WRITE:
        return (
            f"(allow file-read* file-test-existence {path_filter})\n"
            f"(allow file-write* {path_filter})"
        )
    if entry.access is FileSystemAccessMode.EXECUTE:
        return (
            f"(allow file-read* file-test-existence {path_filter})\n"
            f"(allow file-map-executable {path_filter})"
        )
    return _deny_rule(entry, key)


def _deny_rule(
    entry: MaterializedFileSystemPermissionEntry,
    key: str,
    *,
    exception_key: str | None = None,
    exception_keys: tuple[str, ...] = (),
) -> str:
    path_filter = _filter(entry, key)
    keys = tuple(dict.fromkeys((*exception_keys, *(
        (exception_key,) if exception_key is not None else ()
    ))))
    if keys:
        exceptions = " ".join(
            f'(require-not (subpath (param "{value}")))'
            for value in keys
        )
        path_filter = (
            f'(require-all {path_filter} {exceptions})'
        )
    return (
        f"(deny file-read* file-write* file-map-executable "
        f"file-test-existence {path_filter})"
    )
