"""Install fixed, prebuilt Eidos resources without a package manager on the host."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, nullcontext
from graphlib import TopologicalSorter
import hashlib
import os
from pathlib import Path
import stat
import sys
import platform
import errno
import tarfile
import tempfile
import threading
from urllib.parse import urlsplit

import httpx2 as httpx

from eidos_runtime.infrastructure import local_files
from eidos_runtime.infrastructure.runtime_dependencies import (
    RuntimeDependencyCatalog, RuntimeDependencyCatalogError,
)
from eidos_runtime.models.managed_resources import (
    ResourceIndex, ResourcePack, ResourcePhase, ResourceProgress, ResourceStatus,
    resource_archive_name, resource_relative_path,
)
from eidos_runtime.models.runtime_dependencies import validate_windows_resource_path
from eidos_runtime.resource_paths import managed_resource_directory

MAX_INDEX_BYTES = 8 * 1024**2
DOWNLOAD_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})


class ResourceError(RuntimeDependencyCatalogError):
    pass


def default_resource_directory() -> Path:
    return managed_resource_directory()


def load_resource_index(path: Path) -> ResourceIndex:
    try:
        with os.fdopen(local_files.open_file(path), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_INDEX_BYTES:
                raise ValueError("resource index is not a bounded regular file")
            data = stream.read(MAX_INDEX_BYTES + 1)
        if len(data) > MAX_INDEX_BYTES:
            raise ValueError("resource index exceeds its limit")
        return ResourceIndex.model_validate_json(data)
    except (OSError, ValueError) as error:
        raise ResourceError("resource_index_invalid", "resource release description is invalid") from error


class ManagedResourceManager:
    """Process-owned installer; callers run on existing managed Runtime workers.

    The release index is immutable. SQLite AsyncOperations record installation
    outcomes. In-memory values are only download coordination and diagnostics.
    """

    def __init__(
        self, index: ResourceIndex, *, root: Path | None = None, archive_root: Path | None = None,
        installation_operation: Callable[[ResourcePack], AbstractContextManager[None]] | None = None,
        client_factory: Callable[[], httpx.Client] = httpx.Client,
    ) -> None:
        self.index = index
        self.root = (root or default_resource_directory()).absolute()
        self.archive_root = archive_root
        self.installation_operation = installation_operation or (lambda pack: nullcontext())
        self.client_factory = client_factory
        self._packs = {pack.id: pack for pack in index.packs}
        self._condition = threading.Condition()
        self._busy: set[str] = set()
        self._progress: dict[str, ResourceProgress] = {}
        self._verified: dict[str, RuntimeDependencyCatalog] = {}

    def pack(self, pack_id: str) -> ResourcePack:
        try:
            return self._packs[pack_id]
        except KeyError:
            raise ResourceError("resource_unknown", "resource is not in this App release") from None

    def location(self, pack_id: str) -> Path:
        return self.root / self.pack(pack_id).location

    def closure(self, pack_ids: Iterable[str]) -> tuple[str, ...]:
        selected: set[str] = set()

        def visit(pack_id: str) -> None:
            pack = self.pack(pack_id)
            if pack_id not in selected:
                selected.add(pack_id)
                for dep in pack.requires:
                    visit(dep)

        for pack_id in pack_ids:
            visit(pack_id)
        graph = {name: self.pack(name).requires for name in sorted(selected)}
        return tuple(TopologicalSorter(graph).static_order())

    def _phase(self, pack: ResourcePack, phase: ResourcePhase, *, received: int = 0, error: str | None = None) -> None:
        with self._condition:
            self._progress[pack.id] = ResourceProgress(
                id=pack.id, title=pack.title, version=pack.version, phase=phase,
                downloaded_bytes=received, total_bytes=pack.archive_bytes, error_code=error,
            )

    def status(self) -> ResourceStatus:
        return ResourceStatus(
            available=True, directory=str(self.root),
            packs=tuple(self.pack_status(pack.id) for pack in self.index.packs),
        )

    def pack_status(self, pack_id: str) -> ResourceProgress:
        """Report one pack; readiness rechecks that pack's files, not every pack."""
        pack = self.pack(pack_id)
        with self._condition:
            progress = self._progress.get(pack.id)
        if progress is not None:
            return progress
        return ResourceProgress(
            id=pack.id, title=pack.title, version=pack.version,
            phase="ready" if self.is_ready(pack.id) else "missing", total_bytes=pack.archive_bytes,
        )

    def verified_catalog(self, pack_id: str) -> RuntimeDependencyCatalog:
        pack = self.pack(pack_id)
        _verify_platform(pack)
        root = self.location(pack_id)
        _verify_ancestry(root, self.root)
        receipt = root / "runtime.json"
        expected = pack.manifest.model_dump_json(by_alias=True).encode()
        try:
            with os.fdopen(local_files.open_file(receipt), "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(len(expected) + 1) != expected:
                    raise ValueError("receipt differs from the release")
        except (OSError, ValueError):
            raise ResourceError("resource_not_ready", f"{pack.title} is not installed for this release") from None
        with self._condition:
            catalog = self._verified.get(pack_id)
        if catalog is None:
            catalog = RuntimeDependencyCatalog.from_manifest(receipt)
            with self._condition:
                self._verified[pack_id] = catalog
        # Recheck declarations, file types, metadata and executable permissions.
        # Full content checks happen once during installation, not on every Turn.
        catalog.verify_binding(catalog.resolve(None))
        return catalog

    def is_ready(self, pack_id: str) -> bool:
        try:
            self.verified_catalog(pack_id)
            return True
        except (OSError, RuntimeDependencyCatalogError):
            return False

    def ensure(self, pack_ids: Iterable[str], cancel: threading.Event) -> None:
        for pack_id in self.closure(pack_ids):
            self._ensure_one(pack_id, cancel)

    def _ensure_one(self, pack_id: str, cancel: threading.Event) -> None:
        pack = self.pack(pack_id)
        if pack.python_abi and pack.python_abi != f"cp{sys.version_info.major}{sys.version_info.minor}":
            raise ResourceError("resource_python_abi_incompatible", "document libraries require the App's Python 3.12 runtime")
        _verify_platform(pack)
        with self._condition:
            while pack_id in self._busy:
                _check_cancel(cancel)
                self._condition.wait(0.1)
            _check_cancel(cancel)
            if self.is_ready(pack_id):
                self._phase(pack, "ready", received=pack.archive_bytes)
                return
            self._busy.add(pack_id)
        try:
            with self.installation_operation(pack):
                self._phase(pack, "checking")
                self._install(pack, cancel)
                _check_cancel(cancel)
            self._phase(pack, "ready", received=pack.archive_bytes)
        except Exception as error:
            code = error.code if isinstance(error, ResourceError) else "resource_install_failed"
            if isinstance(error, OSError) and error.errno == errno.ENOSPC:
                code = "resource_disk_full"
            self._phase(pack, "failed", error=code)
            if isinstance(error, ResourceError):
                raise
            raise ResourceError(code, f"{pack.title} could not be prepared; retry this operation") from error
        finally:
            with self._condition:
                self._busy.discard(pack_id)
                self._condition.notify_all()

    def _install(self, pack: ResourcePack, cancel: threading.Event) -> None:
        _verify_ancestry(self.root, self.root)
        _private_directory(self.root)
        staging = self.root / ".staging"
        _private_directory(staging)
        with tempfile.TemporaryDirectory(prefix=f"{pack.id}-", dir=staging) as folder:
            stage = Path(folder)
            archive = stage / "payload.tar.gz"
            self._prepare_archive(pack, archive, cancel)
            self._phase(pack, "extracting", received=pack.archive_bytes)
            payload = stage / "payload"
            payload.mkdir(mode=0o700)
            _extract_verified(pack, archive, payload, cancel)
            (payload / "runtime.json").write_bytes(pack.manifest.model_dump_json(by_alias=True).encode())
            RuntimeDependencyCatalog.from_manifest(payload / "runtime.json")
            _check_cancel(cancel)
            destination = self.location(pack.id)
            # Only the install service creates ancestors. New releases always
            # use a different location; repairs restore the same pinned bytes.
            parent = self.root
            for part in Path(pack.location).parts[:-1]:
                parent = parent / part
                _private_directory(parent)
            _verify_ancestry(destination, self.root)
            if destination.exists():
                # A damaged version is moved aside until the new version has
                # been validated, so a failed install retains recovery evidence.
                displaced = stage / "previous"
                destination.rename(displaced)
                try:
                    payload.rename(destination)
                except BaseException:
                    displaced.rename(destination)
                    raise
            else:
                payload.rename(destination)
            with self._condition:
                self._verified.pop(pack.id, None)
            self.verified_catalog(pack.id)

    def _prepare_archive(self, pack: ResourcePack, target: Path, cancel: threading.Event) -> None:
        if pack.url is None:
            if self.archive_root is None:
                raise ResourceError("resource_archive_missing", "resource archive was not included with the App")
            name = resource_archive_name(pack.id, pack.version, pack.manifest.target)
            try:
                descriptor = local_files.open_file(self.archive_root / name)
            except OSError:
                raise ResourceError("resource_archive_missing", "resource archive was not included with the App") from None
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != pack.archive_bytes:
                    raise ResourceError("resource_archive_mismatch", "resource archive does not match this release")
                self._write_archive(pack, target, iter(lambda: stream.read(1024 * 1024), b""), cancel, "verifying")
            return
        self._phase(pack, "downloading")
        with self.client_factory() as client:
            url = pack.url
            for _ in range(6):
                _check_cancel(cancel)
                parsed = urlsplit(url)
                if parsed.scheme != "https" or parsed.hostname not in DOWNLOAD_HOSTS or parsed.username or parsed.password:
                    raise ResourceError("resource_download_redirected", "resource download left the trusted release hosts")
                with client.stream(
                    "GET", url, follow_redirects=False,
                    headers={"Accept-Encoding": "identity", "User-Agent": "Eidos-Resources"},
                    timeout=httpx.Timeout(30, connect=5, read=5),
                ) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers["location"]))
                        continue
                    response.raise_for_status()
                    self._write_archive(pack, target, response.iter_bytes(1024 * 1024), cancel, "downloading")
                    return
        raise ResourceError("resource_download_redirected", "too many resource download redirects")

    def _write_archive(self, pack: ResourcePack, target: Path, chunks: Iterable[bytes],
                       cancel: threading.Event, phase: ResourcePhase) -> None:
        digest, received = hashlib.sha256(), 0
        descriptor = local_files.open_file(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            for chunk in chunks:
                _check_cancel(cancel)
                received += len(chunk)
                if received > pack.archive_bytes:
                    raise ResourceError("resource_archive_too_large", "resource archive exceeded its release size")
                digest.update(chunk)
                output.write(chunk)
                self._phase(pack, phase, received=received)
            output.flush()
            os.fsync(output.fileno())
        self._phase(pack, "verifying", received=received)
        if received != pack.archive_bytes or digest.hexdigest() != pack.archive_sha256:
            raise ResourceError("resource_archive_mismatch", "resource archive does not match this release")


def _verify_platform(pack: ResourcePack) -> None:
    machine = platform.machine().lower()
    if pack.manifest.target == "win32-x64":
        compatible = local_files.WINDOWS and machine in {"amd64", "x86_64"}
    elif local_files.WINDOWS or pack.manifest.executables:
        compatible = sys.platform == "darwin" and machine == "arm64"
    else:
        compatible = True
    if not compatible:
        raise ResourceError("resource_platform_incompatible", "resource release does not match this host")


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise ResourceError("resource_preparation_canceled", "resource preparation was canceled")


def _private_directory(path: Path) -> None:
    # User-owned ancestors outside Resource remain ordinary user directories.
    # Each managed directory itself is private and cannot be a symlink.
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    private = (local_files.has_private_access(path, metadata) if local_files.WINDOWS
               else metadata.st_uid == os.getuid() and not metadata.st_mode & 0o022)
    if not stat.S_ISDIR(metadata.st_mode) or not private:
        raise ResourceError("resource_directory_unsafe", "managed resource directory is unsafe")


def _verify_ancestry(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        raise ResourceError("resource_directory_unsafe", "resource path escapes the managed root")
    current = path
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            pass
        else:
            if (local_files.is_link(metadata) or (local_files.WINDOWS
                    and not local_files.has_private_access(current, metadata))):
                raise ResourceError("resource_directory_unsafe", "managed resource ancestry is unsafe")
        if current == root:
            break
        current = current.parent
    # Canonicalizing the outer root also prevents a symlink in ~/Eidos from
    # silently moving all managed resources to an untrusted location.
    if root.resolve(strict=False) != root:
        raise ResourceError("resource_directory_unsafe", "resource root must have a stable canonical path")


def _extract_verified(pack: ResourcePack, archive: Path, target: Path, cancel: threading.Event) -> None:
    expected = {entry.path: entry.sha256 for entry in pack.manifest.files}
    seen: set[str] = set()
    expanded = 0
    with tarfile.open(archive, "r:gz") as source:
        for count, member in enumerate(source):
            _check_cancel(cancel)
            if count > 65536 or not (member.isdir() or member.isfile()):
                raise ResourceError("resource_archive_invalid", "resource archive contains unsupported entries")
            # Standard-library data filtering owns traversal and link checks.
            filtered = tarfile.data_filter(member, str(target))
            if filtered is None:
                raise ResourceError("resource_archive_invalid", "resource archive entry was rejected")
            normalized = resource_relative_path(member.name.rstrip("/"))
            if pack.manifest.target == "win32-x64":
                try:
                    validate_windows_resource_path(normalized)
                except ValueError:
                    raise ResourceError("resource_archive_invalid", "resource archive uses an unsafe Windows path") from None
            if filtered.name.rstrip("/") != normalized:
                raise ResourceError("resource_archive_invalid", "resource archive path was rewritten")
            if member.isdir():
                (target / normalized).mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            name = member.name
            if name not in expected or name in seen or member.size > 512 * 1024**2:
                raise ResourceError("resource_archive_invalid", "resource file is unexpected or repeated")
            expanded += member.size
            if expanded > pack.expanded_bytes:
                raise ResourceError("resource_archive_too_large", "expanded resource exceeds its release size")
            seen.add(name)
            destination = target / name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            stream = source.extractfile(member)
            if stream is None:
                raise ResourceError("resource_archive_invalid", "resource file has no data")
            digest = hashlib.sha256()
            with stream, destination.open("xb") as output:
                while chunk := stream.read(1024 * 1024):
                    _check_cancel(cancel)
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected[name]:
                raise ResourceError("resource_file_mismatch", "extracted resource does not match the release")
            if not local_files.WINDOWS:
                destination.chmod(0o700 if member.mode & 0o111 else 0o600)
    if seen != expected.keys() or expanded != pack.expanded_bytes:
        raise ResourceError("resource_archive_incomplete", "resource archive is incomplete")
