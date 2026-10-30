from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eidos_runtime.db.database import WorkspaceIdentity


DEFAULT_MAX_ENTRIES = 20_000
DEFAULT_SCAN_SECONDS = 2.0
HASH_LIMIT_BYTES = 1024 * 1024
IGNORED_NAMES = frozenset({
    ".git",
    ".agents",
    ".eidos",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
})


@dataclass(frozen=True)
class WorkspaceManifestEntry:
    path: str
    size: int
    mtime_ns: int
    mode: int
    inode: int | None
    sha256: str | None


@dataclass(frozen=True)
class WorkspaceManifest:
    entries: tuple[WorkspaceManifestEntry, ...]
    complete: bool
    truncated: bool


@dataclass(frozen=True)
class WorkspaceDiff:
    created: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    diff_hash: str
    complete: bool
    truncated: bool

    @property
    def changed(self) -> bool:
        # An incomplete before/after pair cannot distinguish a pre-existing
        # entry from a mutation. Only a complete pair can establish a change.
        return self.complete and bool(self.created or self.modified or self.deleted)


def capture_workspace_manifest(
    root: Path,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    deadline: float | None = None,
) -> WorkspaceManifest:
    root = root.resolve()
    deadline = (
        time.monotonic() + DEFAULT_SCAN_SECONDS
        if deadline is None
        else deadline
    )
    entries: list[WorkspaceManifestEntry] = []
    pending = [("", root)]
    complete = True
    truncated = False
    while pending:
        if time.monotonic() >= deadline:
            complete = False
            truncated = True
            break
        relative_parent, directory = pending.pop()
        try:
            children = sorted(
                os.scandir(directory), key=lambda value: os.fsencode(value.name)
            )
        except OSError:
            complete = False
            continue
        for child in children:
            if child.name in IGNORED_NAMES:
                continue
            if time.monotonic() >= deadline:
                complete = False
                truncated = True
                break
            relative = (
                f"{relative_parent}/{child.name}"
                if relative_parent
                else child.name
            )
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError:
                complete = False
                continue
            if stat.S_ISDIR(metadata.st_mode):
                if not child.is_symlink():
                    pending.append((relative, Path(child.path)))
                continue
            if len(entries) >= max_entries:
                complete = False
                truncated = True
                break
            digest = None
            if stat.S_ISREG(metadata.st_mode) and metadata.st_size <= HASH_LIMIT_BYTES:
                try:
                    digest = _hash_file(Path(child.path), deadline)
                except (OSError, TimeoutError):
                    complete = False
            entries.append(WorkspaceManifestEntry(
                relative,
                metadata.st_size,
                metadata.st_mtime_ns,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_ino or None,
                digest,
            ))
        if truncated:
            break
    return WorkspaceManifest(
        tuple(sorted(entries, key=lambda value: os.fsencode(value.path))),
        complete,
        truncated,
    )


def capture_workspace_roots_manifest(roots: tuple[WorkspaceIdentity, ...]) -> WorkspaceManifest:
    """Compose the existing bounded scanner across the frozen Run roots."""
    from eidos_runtime.sandbox.workspace_index import WorkspaceIndexIncomplete
    from eidos_runtime.tools.workspace import ToolExecutor
    from eidos_runtime.workspace.reader import WorkspacePathError
    import threading
    entries: list[WorkspaceManifestEntry] = []
    seen_paths: set[str] = set()
    complete, truncated = True, False
    deadline = time.monotonic() + DEFAULT_SCAN_SECONDS
    for index, root in enumerate(roots):
        try:
            with ToolExecutor(replace(root, additional_roots=())) as executor:
                executor._verify_root()
                executor.workspace_index.refresh(
                    executor.root_fd, threading.Event(),
                    validate=executor._validate_workspace_index_entry,
                    open_directory=executor._open_directory,
                    deadline=deadline, hash_all=True,
                    max_entries=max(0, DEFAULT_MAX_ENTRIES - len(entries)),
                )
                executor._verify_root()
                manifest = executor.workspace_index.manifest()
        except (OSError, WorkspacePathError, WorkspaceIndexIncomplete):
            manifest = WorkspaceManifest((), False, True)
        for entry in manifest.entries:
            absolute_path = str(root.path / entry.path)
            if absolute_path in seen_paths:
                continue
            seen_paths.add(absolute_path)
            entries.append(
                entry if index == 0 else replace(entry, path=absolute_path)
            )
        complete = complete and manifest.complete
        truncated = truncated or manifest.truncated
    return WorkspaceManifest(tuple(entries), complete, truncated)


def diff_workspace_manifests(
    before: WorkspaceManifest, after: WorkspaceManifest
) -> WorkspaceDiff:
    before_entries = {entry.path: entry for entry in before.entries}
    after_entries = {entry.path: entry for entry in after.entries}
    created = tuple(sorted(after_entries.keys() - before_entries, key=os.fsencode))
    deleted = tuple(sorted(before_entries.keys() - after_entries, key=os.fsencode))
    modified = tuple(sorted(
        (
            path
            for path in before_entries.keys() & after_entries
            if before_entries[path] != after_entries[path]
        ),
        key=os.fsencode,
    ))
    complete = before.complete and after.complete
    truncated = before.truncated or after.truncated
    payload = {
        "created": created,
        "modified": modified,
        "deleted": deleted,
        "complete": complete,
        "truncated": truncated,
    }
    diff_hash = hashlib.sha256(json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return WorkspaceDiff(
        created, modified, deleted, diff_hash, complete, truncated
    )


def attach_workspace_diff(
    result: dict[str, object], diff: WorkspaceDiff
) -> dict[str, object]:
    attached = dict(result)
    data = dict(result.get("data") if isinstance(result.get("data"), dict) else {})
    process_not_started = (
        data.get("termination") == "not_started"
        and result.get("sideEffectsMayExist") is not True
    )
    exit_code = data.get("exitCode")
    execution_known = (
        (data.get("termination") == "exit" or (
            data.get("termination") == "terminated"
            and data.get("executionStatus") == "exited"
            and isinstance(data.get("sessionId"), str)
        ))
        and isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
    )
    managed_running = (
        data.get("executionStatus") == "running"
        and isinstance(data.get("sessionId"), str)
        and bool(data["sessionId"])
    )
    change_state = (
        "unchanged"
        if process_not_started
        else "changed" if diff.changed else "unchanged" if diff.complete else "unknown"
    )
    reported_created = list(diff.created) if diff.complete else []
    reported_modified = list(diff.modified) if diff.complete else []
    reported_deleted = list(diff.deleted) if diff.complete else []
    data.update({
        "commandOutcome": str(result.get("outcome", "error")),
        "workspaceChanged": False if process_not_started else diff.changed,
        "workspaceDiffHash": diff.diff_hash,
        "workspaceManifestComplete": diff.complete,
        "workspaceManifestTruncated": diff.truncated,
        "workspaceDiffIncomplete": not diff.complete,
        "workspaceChangeState": change_state,
        "created": reported_created,
        "modified": reported_modified,
        "deleted": reported_deleted,
    })
    attached["data"] = data
    attached["sideEffectsMayExist"] = (
        result.get("sideEffectsMayExist") is True
        or change_state != "unchanged"
    )
    explicit_reconciliation = result.get("reconciliationRequired")
    if explicit_reconciliation is True:
        reconciliation_required = True
    elif explicit_reconciliation is False and (execution_known or managed_running):
        reconciliation_required = False
    else:
        reconciliation_required = (
            not execution_known
            and (
                change_state == "unknown"
                or result.get("outcome") != "success" and diff.changed
            )
        )
    attached["reconciliationRequired"] = reconciliation_required
    if process_not_started:
        attached["reconciliationRequired"] = False
        attached["sideEffectsMayExist"] = False
    return attached


def _hash_file(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            if time.monotonic() >= deadline:
                raise TimeoutError
            digest.update(chunk)
    return digest.hexdigest()
