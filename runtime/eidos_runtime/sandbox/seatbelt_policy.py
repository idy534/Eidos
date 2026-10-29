from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from eidos_runtime.sandbox.permissions import (
    EffectivePermissionProfile,
    FileSystemAccessMode,
    MaterializedFileSystemPermissionEntry,
    is_approval_protected_write_exception,
    is_approval_write_exception,
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
        self,
        profile: EffectivePermissionProfile,
        *,
        verified_git_metadata: tuple[Path, ...] = (),
        verified_git_checkout: Path | None = None,
    ) -> CompiledSeatbeltPolicy:
        try:
            base = self.base_policy_path.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError("seatbelt base policy is unavailable") from error
        sections = [base.rstrip()]
        parameters: dict[str, str] = {}
        approved_writes: list[tuple[MaterializedFileSystemPermissionEntry, str]] = []
        overrides: list[tuple[MaterializedFileSystemPermissionEntry, str]] = []
        for index, entry in enumerate(
            item for item in profile.entries if item.source == "additional"
        ):
            key = f"ADDITIONAL_{entry.access.value.upper()}_{index}"
            parameters[key] = entry.resolved_path
            sections.append(_metadata_rule(key))
            sections.append(_allow_rule(entry, key))
            if entry in profile.protected_overrides:
                overrides.append((entry, key))
            if entry.access is FileSystemAccessMode.WRITE:
                approved_writes.append((entry, key))
        for index, entry in enumerate(item for item in profile.entries if item.source == "base"):
            if entry.access is FileSystemAccessMode.WRITE and profile.workspace_roots and entry.resolved_path == profile.workspace_roots[0]:
                # The base policy already grants the primary root while excluding
                # its verified Git metadata. A broad allow would undo that scope.
                continue
            key = f"BASE_{entry.access.value.upper()}_{index}"
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
                f'(allow file-map-executable (subpath (param "{key}")))'
            )
        for index, root in enumerate(dict.fromkeys((
            *profile.active_skill_roots, *profile.approval_write_roots,
        ))):
            key = f"APPROVAL_WRITE_ROOT_{index}"
            parameters[key] = root
            write_filter = _exclude_filters(
                f'(subpath (param "{key}"))',
                tuple(_filter(entry, entry_key) for entry, entry_key in approved_writes),
            )
            sections.append(f'(deny file-write* {write_filter})')
        metadata_reads: list[tuple[MaterializedFileSystemPermissionEntry, str]] = []
        git_read_targets = tuple((path, path.is_dir()) for path in verified_git_metadata)
        if verified_git_checkout is not None:
            # Git needs the checkout directory itself to resolve getcwd from
            # a mounted subdirectory. Its unmounted children stay protected.
            git_read_targets += ((verified_git_checkout, False),)
        for index, (path, recursive) in enumerate(git_read_targets):
            if not any(
                path != Path(entry.resolved_path)
                and path.is_relative_to(entry.resolved_path)
                for entry in profile.permanent_denies
            ):
                continue
            key = f"VERIFIED_GIT_READ_{index}"
            parameters[key] = str(path)
            metadata = MaterializedFileSystemPermissionEntry(
                requestedPath=str(path),
                resolvedPath=str(path),
                access=FileSystemAccessMode.READ,
                recursive=recursive,
                source="base",
            )
            metadata_reads.append((metadata, key))
            sections.extend((_metadata_rule(key), _allow_rule(metadata, key)))
        workspace_exceptions: dict[str, tuple[str, ...]] = {}
        for index, workspace_root in enumerate(profile.workspace_roots):
            workspace = Path(workspace_root).resolve(strict=False)
            for entry in profile.permanent_denies:
                protected = Path(entry.resolved_path)
                if protected != workspace and protected in workspace.parents:
                    key = f"WORKSPACE_ROOT_{index}"
                    parameters[key] = str(workspace)
                    workspace_exceptions[entry.resolved_path] = (*workspace_exceptions.get(entry.resolved_path, ()), key)
        for index, entry in enumerate(
            (*profile.permanent_denies, *profile.hard_confidentiality_denies)
        ):
            key = f"PERMANENT_DENY_{index}"
            parameters[key] = entry.resolved_path
            sections.append(
                _deny_rule(
                    entry,
                    key,
                    exception_keys=(
                        (workspace_exceptions.get(entry.resolved_path, ()) if entry.source == "permanent_deny" else ()) + tuple(
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
                    exception_filters=tuple(
                        _filter(allowed, allowed_key)
                        for allowed, allowed_key in approved_writes
                        if entry.source == "permanent_deny" and is_approval_write_exception(
                            Path(allowed.resolved_path), Path(entry.resolved_path), profile.approval_write_roots,
                        )
                    ),
                    metadata_exceptions=(
                        tuple(f'(path-ancestors (param "{value}"))' for value in workspace_exceptions[entry.resolved_path])
                        if entry.source == "permanent_deny" and entry.resolved_path in workspace_exceptions
                        else ()
                    ) + tuple(
                        f'(path-ancestors (param "{allowed_key}"))'
                        for allowed, allowed_key in approved_writes
                        if entry.source == "permanent_deny" and is_approval_write_exception(
                            Path(allowed.resolved_path), Path(entry.resolved_path), profile.approval_write_roots,
                        )
                    ),
                    protected_overrides=() if any(
                        item.source == "additional" and item.access is FileSystemAccessMode.DENY
                        and item.resolved_path == entry.resolved_path
                        for item in profile.entries
                    ) else (
                        *overrides,
                        *(metadata_reads if entry.source == "permanent_deny" else ()),
                    ),
                )
            )
        for index, path in enumerate(profile.protected_write_paths):
            key = f"PROTECTED_WRITE_{index}"
            parameters[key] = path
            write_filter = _exclude_filters(
                f'(subpath (param "{key}"))',
                tuple(
                    _filter(allowed, allowed_key)
                    for allowed, allowed_key in approved_writes
                    if is_approval_protected_write_exception(
                        Path(allowed.resolved_path),
                        Path(path),
                        profile.approval_write_roots,
                    ) or any(allowed == override for override, _key in overrides)
                ),
            )
            sections.append(
                f'(deny file-write* {write_filter})'
            )
        if overrides:
            # Keep the fixed interpreter protection, with only the approved
            # write targets excluded for this execution.
            python_deny = '(deny file-write*\n  (subpath (param "PYTHON_RUNTIME_ROOT"))\n  (subpath (param "PYTHON_BASE_RUNTIME_ROOT")))'
            exclusions = tuple(_filter(entry, key) for entry, key in overrides
                               if entry.access is FileSystemAccessMode.WRITE)
            if exclusions:
                python_filter = _exclude_filters(
                    '(require-any (subpath (param "PYTHON_RUNTIME_ROOT")) (subpath (param "PYTHON_BASE_RUNTIME_ROOT")))',
                    exclusions,
                )
                sections[0] = sections[0].replace(python_deny, f'(deny file-write* {python_filter})')
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
    exception_filters: tuple[str, ...] = (),
    metadata_exceptions: tuple[str, ...] = (),
    protected_overrides: tuple[tuple[MaterializedFileSystemPermissionEntry, str], ...] = (),
) -> str:
    path_filter = _filter(entry, key)
    keys = tuple(dict.fromkeys((*exception_keys, *(
        (exception_key,) if exception_key is not None else ()
    ))))
    path_filter = _exclude_filters(path_filter, (
        *(f'(subpath (param "{value}"))' for value in keys),
        *exception_filters,
    ))
    if protected_overrides:
        reads = tuple(_filter(allowed, allowed_key) for allowed, allowed_key in protected_overrides)
        ancestors = tuple(f'(path-ancestors (param "{allowed_key}"))' for _allowed, allowed_key in protected_overrides)
        writes = tuple(_filter(allowed, allowed_key) for allowed, allowed_key in protected_overrides
                       if allowed.access is FileSystemAccessMode.WRITE)
        executes = tuple(_filter(allowed, allowed_key) for allowed, allowed_key in protected_overrides
                         if allowed.access is FileSystemAccessMode.EXECUTE)
        return (
            f'(deny file-read-data file-read-xattr {_exclude_filters(path_filter, reads)})\n'
            f'(deny file-read-metadata file-test-existence {_exclude_filters(path_filter, (*reads, *ancestors, *metadata_exceptions))})\n'
            f'(deny file-write* {_exclude_filters(path_filter, writes)})\n'
            f'(deny file-map-executable {_exclude_filters(path_filter, executes)})'
        )
    if metadata_exceptions:
        # An inherited directory fd still needs identity checks. Allow ancestor
        # metadata, never ancestor contents, enumeration, xattrs or writes.
        metadata_filter = _exclude_filters(path_filter, metadata_exceptions)
        return (
            f"(deny file-read-data file-read-xattr file-write* file-map-executable {path_filter})\n"
            f"(deny file-read-metadata file-test-existence {metadata_filter})"
        )
    return (
        f"(deny file-read* file-write* file-map-executable "
        f"file-test-existence {path_filter})"
    )


def _exclude_filters(path_filter: str, exceptions: tuple[str, ...]) -> str:
    if not exceptions:
        return path_filter
    excluded = " ".join(f"(require-not {value})" for value in exceptions)
    return f"(require-all {path_filter} {excluded})"
