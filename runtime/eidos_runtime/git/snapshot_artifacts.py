from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from eidos_runtime.git.models import GitWorkingTreePatch


FORMAT_VERSION = 1


@dataclass(frozen=True)
class SnapshotArtifact:
    path: Path
    artifact_sha256: str
    full_patch_sha256: str
    staged_patch_sha256: str
    format_version: int = FORMAT_VERSION


class SnapshotArtifactStore:
    """Crash-safe filesystem store for compressed Git patch artifacts."""

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
        artifact_sha256 = _sha256(full_compressed + b"\0" + staged_compressed)
        artifact = SnapshotArtifact(
            path=target,
            artifact_sha256=artifact_sha256,
            full_patch_sha256=_sha256(full),
            staged_patch_sha256=_sha256(staged),
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
            manifest = {
                "formatVersion": FORMAT_VERSION,
                "artifactSha256": artifact_sha256,
                "fullPatchSha256": artifact.full_patch_sha256,
                "stagedPatchSha256": artifact.staged_patch_sha256,
            }
            self._write_bytes(
                temporary / "manifest.json",
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(
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
            if _sha256(full_compressed + b"\0" + staged_compressed) != manifest.artifact_sha256:
                raise ValueError("snapshot artifact checksum mismatch")
            full = gzip.decompress(full_compressed)
            staged = gzip.decompress(staged_compressed)
            if _sha256(full) != manifest.full_patch_sha256:
                raise ValueError("snapshot full patch checksum mismatch")
            if _sha256(staged) != manifest.staged_patch_sha256:
                raise ValueError("snapshot staged patch checksum mismatch")
            return GitWorkingTreePatch(
                full_patch=full.decode("utf-8"),
                staged_patch=staged.decode("utf-8"),
            )
        except (OSError, UnicodeDecodeError, gzip.BadGzipFile, json.JSONDecodeError) as error:
            raise ValueError("snapshot artifact is unreadable") from error

    def verify(self, artifact_path: str | Path, artifact_sha256: str) -> SnapshotArtifact:
        target = self._validate_target(Path(artifact_path))
        manifest = self._read_manifest(target)
        if manifest.artifact_sha256 != artifact_sha256:
            raise ValueError("snapshot artifact checksum mismatch")
        self.read(target)
        return manifest

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
        if (
            not snapshot_id
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in snapshot_id)
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
            value = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            return SnapshotArtifact(
                path=target,
                artifact_sha256=str(value["artifactSha256"]),
                full_patch_sha256=str(value["fullPatchSha256"]),
                staged_patch_sha256=str(value["stagedPatchSha256"]),
                format_version=int(value["formatVersion"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("snapshot artifact manifest is invalid") from error


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["FORMAT_VERSION", "SnapshotArtifact", "SnapshotArtifactStore"]
