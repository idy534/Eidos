from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from pydantic import Field, ValidationError

from eidos_runtime.git.models import GitWorkingTreePatch
from eidos_runtime.models import EidosFrozenStrictModel


FORMAT_VERSION = 1
_SHA256 = r"^[0-9a-f]{64}$"


class SnapshotArtifactManifest(EidosFrozenStrictModel):
    format_version: int = Field(default=FORMAT_VERSION, ge=1, le=100)
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=_SHA256)
    full_patch_sha256: str = Field(min_length=64, max_length=64, pattern=_SHA256)
    staged_patch_sha256: str = Field(min_length=64, max_length=64, pattern=_SHA256)
    state_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=_SHA256
    )


class SnapshotArtifact(EidosFrozenStrictModel):
    path: Path
    artifact_sha256: str = Field(min_length=64, max_length=64, pattern=_SHA256)
    full_patch_sha256: str = Field(min_length=64, max_length=64, pattern=_SHA256)
    staged_patch_sha256: str = Field(min_length=64, max_length=64, pattern=_SHA256)
    format_version: int = Field(default=FORMAT_VERSION, ge=1, le=100)
    state_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=_SHA256
    )


class SnapshotArtifactStore:
    """Crash-safe filesystem store for compressed snapshot state."""

    def __init__(self, data_directory: Path) -> None:
        if not data_directory.is_absolute():
            raise ValueError("snapshot data directory must be absolute")
        self.root = (data_directory / "worktree-snapshots").resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def write(self, snapshot_id: str, changes: GitWorkingTreePatch) -> SnapshotArtifact:
        target = self._target(snapshot_id)
        full = changes.full_patch.encode("utf-8")
        staged = changes.staged_patch.encode("utf-8")
        full_compressed = gzip.compress(full, mtime=0)
        staged_compressed = gzip.compress(staged, mtime=0)
        state = changes.model_dump_json(by_alias=True, exclude_none=False).encode(
            "utf-8"
        )
        state_compressed = gzip.compress(state, mtime=0)
        state_sha256 = _sha256(state) if _has_structured_state(changes) else None
        artifact_sha256 = _artifact_hash(
            full_compressed,
            staged_compressed,
            state_compressed if state_sha256 is not None else None,
        )
        artifact = SnapshotArtifact(
            path=target,
            artifact_sha256=artifact_sha256,
            full_patch_sha256=_sha256(full),
            staged_patch_sha256=_sha256(staged),
            state_sha256=state_sha256,
        )
        if target.exists():
            existing = self._read_manifest(target)
            if existing != artifact:
                raise ValueError("snapshot artifact identity conflict")
            return artifact

        temporary = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}-", dir=self.root))
        try:
            self._write_bytes(temporary / "full.patch.gz", full_compressed)
            self._write_bytes(temporary / "staged.patch.gz", staged_compressed)
            if state_sha256 is not None:
                self._write_bytes(temporary / "working-state.json.gz", state_compressed)
            manifest = SnapshotArtifactManifest(
                format_version=FORMAT_VERSION,
                artifact_sha256=artifact_sha256,
                full_patch_sha256=artifact.full_patch_sha256,
                staged_patch_sha256=artifact.staged_patch_sha256,
                state_sha256=state_sha256,
            )
            self._write_bytes(
                temporary / "manifest.json",
                manifest.model_dump_json(by_alias=True, exclude_none=True).encode(
                    "utf-8"
                ),
            )
            _fsync_directory(temporary)
            os.replace(temporary, target)
            _fsync_directory(self.root)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return artifact

    def read(self, artifact_path: str | Path) -> GitWorkingTreePatch:
        target = self._validate_target(Path(artifact_path))
        manifest = self._read_manifest(target)
        try:
            full_compressed = (target / "full.patch.gz").read_bytes()
            staged_compressed = (target / "staged.patch.gz").read_bytes()
            state_compressed: bytes | None = None
            if manifest.state_sha256 is not None:
                state_compressed = (target / "working-state.json.gz").read_bytes()
            if (
                _artifact_hash(full_compressed, staged_compressed, state_compressed)
                != manifest.artifact_sha256
            ):
                raise ValueError("snapshot artifact checksum mismatch")
            full = gzip.decompress(full_compressed)
            staged = gzip.decompress(staged_compressed)
            if _sha256(full) != manifest.full_patch_sha256:
                raise ValueError("snapshot full patch checksum mismatch")
            if _sha256(staged) != manifest.staged_patch_sha256:
                raise ValueError("snapshot staged patch checksum mismatch")
            if state_compressed is None:
                return GitWorkingTreePatch(
                    full_patch=full.decode("utf-8"),
                    staged_patch=staged.decode("utf-8"),
                )
            state = gzip.decompress(state_compressed)
            if _sha256(state) != manifest.state_sha256:
                raise ValueError("snapshot state checksum mismatch")
            parsed = GitWorkingTreePatch.model_validate_json(state)
            if parsed.full_patch != full.decode("utf-8") or parsed.staged_patch != staged.decode(
                "utf-8"
            ):
                raise ValueError("snapshot state patch mismatch")
            return parsed
        except (OSError, UnicodeDecodeError, UnicodeError, gzip.BadGzipFile, ValidationError) as error:
            raise ValueError("snapshot artifact is unreadable") from error

    def verify(self, artifact_path: str | Path, artifact_sha256: str) -> SnapshotArtifact:
        target = self._validate_target(Path(artifact_path))
        manifest = self._read_manifest(target)
        if manifest.artifact_sha256 != artifact_sha256:
            raise ValueError("snapshot artifact checksum mismatch")
        self.read(target)
        return _artifact_from_manifest(target, manifest)

    def delete(self, artifact_path: str | Path) -> None:
        target = self._validate_target(Path(artifact_path))
        if not target.exists():
            return
        if not target.is_dir() or target.is_symlink():
            raise ValueError("snapshot artifact path is unsafe")
        shutil.rmtree(target)
        _fsync_directory(self.root)

    def list_directories(self) -> tuple[Path, ...]:
        return tuple(
            path
            for path in sorted(self.root.iterdir())
            if path.is_dir() and not path.is_symlink()
        )

    def _target(self, snapshot_id: str) -> Path:
        if not snapshot_id or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in snapshot_id
        ):
            raise ValueError("snapshot id is unsafe")
        return self._validate_target(self.root / snapshot_id)

    def _validate_target(self, target: Path) -> Path:
        absolute = target.absolute()
        if absolute.parent != self.root or absolute == self.root:
            raise ValueError("snapshot artifact path is outside the store")
        if absolute.is_symlink():
            raise ValueError("snapshot artifact path is unsafe")
        return absolute

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        with path.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _read_manifest(self, target: Path) -> SnapshotArtifact:
        try:
            manifest = SnapshotArtifactManifest.model_validate_json(
                (target / "manifest.json").read_bytes()
            )
        except (OSError, UnicodeDecodeError, ValidationError, ValueError) as error:
            raise ValueError("snapshot artifact manifest is invalid") from error
        return _artifact_from_manifest(target, manifest)


def _artifact_from_manifest(
    path: Path, manifest: SnapshotArtifactManifest
) -> SnapshotArtifact:
    return SnapshotArtifact(
        path=path,
        artifact_sha256=manifest.artifact_sha256,
        full_patch_sha256=manifest.full_patch_sha256,
        staged_patch_sha256=manifest.staged_patch_sha256,
        format_version=manifest.format_version,
        state_sha256=manifest.state_sha256,
    )


def _has_structured_state(changes: GitWorkingTreePatch) -> bool:
    return changes.full_state is not None or changes.staged_state is not None


def _artifact_hash(
    full_compressed: bytes,
    staged_compressed: bytes,
    state_compressed: bytes | None,
) -> str:
    value = full_compressed + b"\0" + staged_compressed
    if state_compressed is not None:
        value += b"\0" + state_compressed
    return _sha256(value)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "FORMAT_VERSION",
    "SnapshotArtifact",
    "SnapshotArtifactManifest",
    "SnapshotArtifactStore",
]
