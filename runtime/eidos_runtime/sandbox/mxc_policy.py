"""Compile a restricted Shell policy for the native MXC ProcessContainer."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import subprocess

from jsonschema import Draft7Validator
from pydantic import JsonValue
from referencing import Registry

from eidos_runtime.infrastructure import local_files
from eidos_runtime.sandbox.permissions import EffectivePermissionProfile, FileSystemAccessMode


MAX_MXC_REQUEST_BYTES = 1024 * 1024
_MAX_POLICY_PATHS = 256
_MXC_SCHEMA = Path(__file__).with_name("mxc-config.schema.1.0.0.json")
# Published schema from microsoft/mxc at v1.1.0, Git blob 8bb245450b943012febc421530c6a699d6395ed0.
MXC_SCHEMA_SHA256 = "207254fa94966a800fa70f01a2da73ae813d27fe8847200c92f4dd0e219649c2"
_METADATA_NAMES = (".git", ".agents", ".eidos")


class MxcPolicyError(ValueError):
    """The request cannot be represented by a restricted MXC policy."""


@dataclass(frozen=True)
class MxcPathInfo:
    directory: bool
    device: int
    file_id: int


@dataclass(frozen=True)
class MxcPolicy:
    readwrite: tuple[str, ...]
    readonly: tuple[str, ...]
    denied: tuple[str, ...]
    network_enabled: bool
    sandbox_home: str
    sandbox_tmp: str
    _pins: tuple[tuple[PureWindowsPath, MxcPathInfo], ...]

    @property
    def readonly_pins(self) -> tuple[Path, ...]:
        return tuple(Path(path) for path, _info in self._pins)

    @contextmanager
    def pin_readonly_paths(self) -> Iterator[None]:
        """Hold nested read-only targets so a writable parent cannot replace them."""
        with ExitStack() as stack:
            try:
                for path, expected in self._pins:
                    opener = local_files.open_directory if expected.directory else local_files.open_file
                    descriptor = opener(Path(path))
                    stack.callback(os.close, descriptor)
                    metadata = os.fstat(descriptor)
                    if (metadata.st_dev, metadata.st_ino) != (expected.device, expected.file_id):
                        raise MxcPolicyError("MXC protected path changed before launch")
            except OSError:
                raise MxcPolicyError("MXC protected path cannot be held for execution") from None
            yield

    @property
    def profile_hash(self) -> str:
        return hashlib.sha256(_encode(self._policy())).hexdigest()

    def request(
        self, argv: Sequence[str], cwd: Path | PureWindowsPath, environment: Mapping[str, str],
    ) -> bytes:
        if not argv or any("\x00" in argument for argument in argv):
            raise MxcPolicyError("invalid MXC command arguments")
        _local_path(argv[0])
        command = subprocess.list2cmdline(argv)
        try:
            if len(command.encode("utf-16-le")) >= 32767 * 2:
                raise MxcPolicyError("MXC command exceeds the Windows command-line limit")
        except UnicodeEncodeError:
            raise MxcPolicyError("invalid MXC command encoding") from None
        request = self._policy()
        request["process"] = {
            "commandLine": command,
            "cwd": str(_local_path(cwd)),
            "env": _request_environment(environment, home=self.sandbox_home, tmp=self.sandbox_tmp),
            "inheritDefaultEnv": False,
            "timeout": 0,
        }
        encoded = _encode(request)
        if len(encoded) > MAX_MXC_REQUEST_BYTES:
            raise MxcPolicyError("MXC request exceeds the size limit")
        if not _validator().is_valid(request):
            raise MxcPolicyError("invalid MXC configuration")
        return encoded

    def _policy(self) -> dict[str, JsonValue]:
        return {
            "version": "1.0.0",
            "containment": "processcontainer",
            "filesystem": {
                "readwritePaths": list(self.readwrite),
                "readonlyPaths": list(self.readonly),
                "deniedPaths": list(self.denied),
            },
            "network": {
                "egress": {"default": "allow" if self.network_enabled else "deny"},
                "ingress": {"default": "deny", "hostLoopback": "deny"},
            },
            "ui": {"disable": False, "clipboard": "none", "injection": False},
        }


def compile_mxc_policy(
    permissions: EffectivePermissionProfile,
    *,
    sandbox_home: Path | PureWindowsPath,
    sandbox_tmp: Path | PureWindowsPath,
    git_worktree_dir: Path | PureWindowsPath | None = None,
    git_common_dir: Path | PureWindowsPath | None = None,
    readonly_roots: Callable[[], Iterable[str | PureWindowsPath]] | None = None,
    inspect_path: Callable[[PureWindowsPath], MxcPathInfo | None] | None = None,
) -> MxcPolicy:
    if permissions.full_access:
        raise MxcPolicyError("full access cannot be compiled as MXC")
    workspace = {_local_path(path) for path in permissions.workspace_roots}
    scratch = {_local_path(path) for path in (sandbox_home, sandbox_tmp)}
    git = {_local_path(path) for path in (git_worktree_dir, git_common_dir) if path is not None}
    skills = {_local_path(path) for path in permissions.active_skill_roots}
    approval_roots = {_local_path(path) for path in permissions.approval_write_roots}
    protected_write = {_local_path(path) for path in permissions.protected_write_paths}
    protected_write.update(git | skills | approval_roots)
    protected_write.update(root / name for root in workspace for name in _METADATA_NAMES)
    permanent = {_local_path(entry.resolved_path) for entry in permissions.permanent_denies}
    hard = {_local_path(entry.resolved_path) for entry in permissions.hard_confidentiality_denies}
    explicit = {_local_path(entry.resolved_path) for entry in permissions.entries
                if entry.access is FileSystemAccessMode.DENY}
    check = lru_cache(maxsize=_MAX_POLICY_PATHS)(inspect_path or _inspect_local_path)

    read: set[PureWindowsPath] = set()
    write: set[PureWindowsPath] = set()
    approved_exact_denies: set[PureWindowsPath] = set()
    for path in workspace | scratch:
        info = check(path)
        if info is None or not info.directory:
            raise MxcPolicyError("MXC workspace or scratch directory is unavailable")
    for path in git:
        if check(path) is None:
            raise MxcPolicyError("verified Git metadata is unavailable")

    for entry in permissions.entries:
        path = _local_path(entry.resolved_path)
        info = check(path)
        if not entry.recursive and (info is None or info.directory):
            raise MxcPolicyError("MXC cannot apply an exact scope to a directory")
        if entry.access is FileSystemAccessMode.DENY:
            continue
        if info is None:
            raise MxcPolicyError("MXC grant path is unavailable")
        if _within(path, explicit):
            continue
        override = entry in permissions.protected_overrides
        can_write = entry.access is FileSystemAccessMode.WRITE
        ordinary_write = entry.source == "additional" and can_write
        if _within(path, hard) and not override:
            continue
        if any(path.is_relative_to(blocked) and not (
            override or _workspace_exception(path, blocked, workspace)
            or (path != blocked and path in skills)
            or (ordinary_write and any(
                path.is_relative_to(root) and root.is_relative_to(blocked)
                for root in approval_roots
            ))
        ) for blocked in permanent):
            continue
        if not _within(path, workspace) and path in scratch:
            raise MxcPolicyError("MXC scratch directory intersects a permission grant")
        if can_write and any(path.is_relative_to(protected) and not (
            override or (ordinary_write and protected in approval_roots)
        ) for protected in protected_write):
            can_write = False
        if (override or ordinary_write and path in approval_roots) and path in permanent | hard:
            approved_exact_denies.add(path)
        (write if can_write else read).add(path)

    blocked = (permanent | hard) - approved_exact_denies | explicit
    if any(_within(path, blocked | protected_write) for path in scratch):
        raise MxcPolicyError("MXC scratch directory intersects a protected path")
    write.update(scratch)
    read.update(path for path in git if not _within(path, hard | explicit) and path not in permanent)
    authorized = read | write
    for value in permissions.runtime_roots:
        path = _local_path(value)
        if not _within(path, blocked):
            read.add(path)
    for value in (readonly_roots or _local_readonly_roots)():
        path = _local_path(value)
        # A broad baseline grant must not reopen a denied subtree.
        if not _within(path, blocked):
            read.add(path)
            if len(read) > _MAX_POLICY_PATHS:
                raise MxcPolicyError("MXC policy exceeds the filesystem path limit")
    pins: list[tuple[PureWindowsPath, MxcPathInfo]] = []
    for path in protected_write - blocked - write:
        if any(path.is_relative_to(parent) and not _workspace_exception(path, parent, authorized)
               for parent in blocked):
            continue
        info = check(path)
        if info is None:
            # An MXC read-only grant for an absent path leaves creation allowed.
            if any(root.is_relative_to(path) for root in authorized):
                raise MxcPolicyError("MXC protected path changed during policy preparation")
            blocked.add(path)
        else:
            read.add(path)
            if _within(path, write):
                pins.append((path, info))
    read.difference_update(write | blocked)
    # An extra read permission must not narrow existing write access. Retain
    # read-only carveouts and explicit reads below a more specific denial.
    read = {
        path for path in read
        if not any(path.is_relative_to(root) and not any(
            parent != root and parent.is_relative_to(root) and path.is_relative_to(parent)
            for parent in blocked | protected_write
        ) for root in write)
    }
    if len(read) + len(write) + len(blocked) > _MAX_POLICY_PATHS:
        raise MxcPolicyError("MXC policy exceeds the filesystem path limit")
    return MxcPolicy(
        _sorted_paths(write), _sorted_paths(read), _sorted_paths(blocked), permissions.network_enabled,
        str(_local_path(sandbox_home)), str(_local_path(sandbox_tmp)),
        tuple(sorted(pins, key=lambda pin: str(pin[0]).lower())),
    )


def _workspace_exception(
    path: PureWindowsPath, protected: PureWindowsPath, roots: set[PureWindowsPath],
) -> bool:
    return any(root != protected and root.is_relative_to(protected) and path.is_relative_to(root)
               for root in roots)


def _within(path: PureWindowsPath, roots: Iterable[PureWindowsPath]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _local_path(value: str | Path | PureWindowsPath) -> PureWindowsPath:
    path = PureWindowsPath(value)
    if (
        not path.is_absolute()
        or len(path.drive) != 2
        or path.drive[1] != ":"
        or not (path.drive[0].isascii() and path.drive[0].isalpha())
        or path == path.parent
        or any(
            part == ".." or part[-1] in ". " or PureWindowsPath(part).is_reserved()
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            for part in path.parts[1:]
        )
    ):
        raise MxcPolicyError("MXC requires canonical local paths below a drive root")
    return path


def _inspect_local_path(path: PureWindowsPath) -> MxcPathInfo | None:
    if not local_files.WINDOWS:
        raise MxcPolicyError("MXC path inspection requires Windows")
    target = Path(path)
    try:
        # The same local NTFS checks used for Eidos's verified file operations
        # also exclude removable, network and non-NTFS mounts here.
        os.close(local_files.open_directory(target.anchor))
        for ancestor in reversed(target.parents):
            if local_files.is_link(ancestor.lstat()):
                raise MxcPolicyError("MXC path includes a reparse point")
        metadata = target.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise MxcPolicyError("MXC path cannot be inspected") from None
    if local_files.is_link(metadata) or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
        raise MxcPolicyError("MXC path is not a plain file or directory")
    return MxcPathInfo(stat.S_ISDIR(metadata.st_mode), metadata.st_dev, metadata.st_ino)


def _local_readonly_roots() -> Iterator[str]:
    if not local_files.WINDOWS:
        raise MxcPolicyError("MXC drive enumeration requires Windows")
    from eidos_runtime.infrastructure.windows_files import logical_drive_roots

    for root in logical_drive_roots():
        try:
            descriptor = local_files.open_directory(root)
        except (FileNotFoundError, PermissionError):
            # Unavailable, non-local and non-NTFS volumes receive no grant.
            continue
        try:
            with os.scandir(root) as children:
                for child in children:
                    try:
                        metadata = child.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not local_files.is_link(metadata) and (
                        stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
                    ):
                        yield child.path
        except OSError:
            raise MxcPolicyError("MXC cannot enumerate a local drive") from None
        finally:
            os.close(descriptor)


def _sorted_paths(paths: Iterable[PureWindowsPath]) -> tuple[str, ...]:
    return tuple(sorted(map(str, paths), key=str.lower))


def _request_environment(environment: Mapping[str, str], *, home: str, tmp: str) -> list[str]:
    result = {name.upper(): value for name, value in environment.items()}
    if any(not name or "=" in name or "\x00" in name + value for name, value in result.items()):
        raise MxcPolicyError("invalid MXC environment entry")
    root = result.get("SYSTEMROOT") or os.environ.get("SYSTEMROOT")
    local_appdata = result.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA")
    if not root or not local_appdata:
        raise MxcPolicyError("MXC requires SYSTEMROOT and LOCALAPPDATA")
    result.update(
        SYSTEMROOT=str(_local_path(root)),
        LOCALAPPDATA=str(_local_path(local_appdata)),
        HOME=home, TMPDIR=tmp, TMP=tmp, TEMP=tmp,
    )
    return [f"{name}={value}" for name, value in sorted(result.items())]


def _encode(document: dict[str, JsonValue]) -> bytes:
    try:
        return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        raise MxcPolicyError("invalid MXC request encoding") from None


@lru_cache(maxsize=1)
def _validator() -> Draft7Validator:
    try:
        with _MXC_SCHEMA.open("rb") as source:
            content = source.read(64 * 1024).replace(b"\r\n", b"\n")
    except OSError:
        raise MxcPolicyError("MXC schema unavailable") from None
    if hashlib.sha256(content).hexdigest() != MXC_SCHEMA_SHA256:
        raise MxcPolicyError("MXC schema integrity check failed")
    # The pinned schema has only local references, and the Registry has no retriever.
    return Draft7Validator(json.loads(content), registry=Registry())
