from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from eidos_runtime.git import native
from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.models import GitWorkingTreePatch
from eidos_runtime.git.refs import GitRefValidator
from eidos_runtime.git.errors import GitUnsupportedOperationError
from eidos_runtime.git.snapshot_artifacts import (
    SnapshotArtifactManifest,
    SnapshotArtifactStore,
)
from eidos_runtime.application.worktree_retention_policy import (
    RetentionCandidate,
    WorktreeRetentionPolicy,
)


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_bytes(cwd: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repository / "binary.dat").write_bytes(b"base\x00\xff\n")
    _git(repository, "add", "tracked.txt", "binary.dat")
    _git(repository, "commit", "-qm", "base")
    return repository


def test_native_git_surface_is_one_hardened_cli_adapter() -> None:
    removed = {
        "NativeBranchAttacher",
        "NativeWorktreeCheckout",
        "NativeWorktreeChangeTransfer",
        "NativeWorktreeCleaner",
        "NativeWorktreeHandoffCleaner",
        "NativeWorktreeRetentionCleaner",
        "NativeWorktreeCreator",
    }
    assert removed.isdisjoint(vars(native))
    assert native.GitCli is not None


def test_git_ref_validator_delegates_branch_grammar_to_dulwich() -> None:
    assert GitRefValidator.branch("feature/中文") == "refs/heads/feature/中文".encode()
    for invalid in ("feature..bad", "feature@{bad}", "feature/.", "feature.lock"):
        with pytest.raises(ValueError):
            GitRefValidator.branch(invalid)


def test_capture_and_apply_preserve_binary_mode_symlink_and_index_state(
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path)
    (source / "mode.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (source / "mode.sh").chmod(0o755)
    _git(source, "add", "mode.sh")
    _git(source, "commit", "-qm", "add executable")
    (source / "staged-deleted.txt").write_text("staged delete\n", encoding="utf-8")
    _git(source, "add", "staged-deleted.txt")
    _git(source, "commit", "-qm", "add staged deletion fixture")
    (source / "mode.sh").chmod(0o644)
    (source / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    (source / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (source / "binary.dat").write_bytes(b"staged\x00\x01\xff\n")
    _git(source, "add", "binary.dat")
    (source / "binary.dat").write_bytes(b"changed\x00\x01\xff\n")
    (source / "binary.dat").chmod(0o755)
    (source / "link.txt").symlink_to("tracked.txt")
    (source / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    _git(source, "add", "deleted.txt")
    (source / "deleted.txt").unlink()
    (source / "staged-deleted.txt").unlink()
    _git(source, "add", "staged-deleted.txt")
    (source / "untracked.dat").write_bytes(b"untracked\x00\xff")
    (source / "staged-empty.txt").write_bytes(b"")
    _git(source, "add", "staged-empty.txt")
    (source / "untracked-empty.txt").write_bytes(b"")
    (source / "nested").mkdir()
    (source / "nested" / "文件.txt").write_text("unicode\n", encoding="utf-8")

    source_head = _git(source, "rev-parse", "HEAD")
    source_index = (source / ".git" / "index").read_bytes()
    source_refs = tuple(
        sorted(
            (str(path.relative_to(source / ".git")), path.read_bytes())
            for path in (source / ".git" / "refs").rglob("*")
            if path.is_file()
        )
    )
    source_binary_sha = hashlib.sha256((source / "binary.dat").read_bytes()).hexdigest()

    backend = DulwichGitBackend()
    changes = backend.capture_worktree_changes(source)
    assert set(GitWorkingTreePatch.model_fields) == {"full_patch", "staged_patch"}
    assert isinstance(changes.full_patch, bytes)
    assert isinstance(changes.staged_patch, bytes)
    assert GitWorkingTreePatch.model_validate_json(changes.model_dump_json()) == changes

    assert _git(source, "rev-parse", "HEAD") == source_head
    assert (source / ".git" / "index").read_bytes() == source_index
    assert source_refs == tuple(
        sorted(
            (str(path.relative_to(source / ".git")), path.read_bytes())
            for path in (source / ".git" / "refs").rglob("*")
            if path.is_file()
        )
    )
    assert hashlib.sha256((source / "binary.dat").read_bytes()).hexdigest() == source_binary_sha

    target = tmp_path / "target"
    backend.worktree_add(source, target, None, backend.head(source))
    backend.apply_worktree_changes(target, changes)

    assert (target / "tracked.txt").read_text(encoding="utf-8") == "unstaged\n"
    assert (target / "binary.dat").read_bytes() == b"changed\x00\x01\xff\n"
    assert (target / "binary.dat").stat().st_mode & 0o111
    assert _git_bytes(target, "show", ":binary.dat") == b"staged\x00\x01\xff\n"
    assert (target / "link.txt").is_symlink()
    assert os.readlink(target / "link.txt") == "tracked.txt"
    assert not (target / "deleted.txt").exists()
    assert not (target / "staged-deleted.txt").exists()
    assert (target / "untracked.dat").read_bytes() == b"untracked\x00\xff"
    assert (target / "staged-empty.txt").read_bytes() == b""
    assert (target / "untracked-empty.txt").read_bytes() == b""
    assert not (target / "mode.sh").stat().st_mode & 0o111
    assert (target / "nested" / "文件.txt").read_text(encoding="utf-8") == "unicode\n"
    assert "tracked.txt" in backend.status(target).staged_paths


def test_snapshot_artifact_is_patch_only_and_scales_with_changed_content(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    for index in range(5000):
        path = repository / "tracked" / f"file-{index}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text("unchanged\n", encoding="utf-8")
    _git(repository, "add", "tracked")
    _git(repository, "commit", "-qm", "large baseline")
    (repository / "tracked" / "file-2500.txt").write_text("changed\n", encoding="utf-8")

    changes = DulwichGitBackend().capture_worktree_changes(repository)
    artifact = SnapshotArtifactStore(tmp_path / "data").write("snapshot", changes)
    names = {path.name for path in artifact.path.iterdir()}

    assert names == {"full.patch.gz", "staged.patch.gz", "manifest.json"}
    assert len(changes.full_patch) < 16 * 1024
    artifact_bytes = sum(path.stat().st_size for path in artifact.path.iterdir())
    assert artifact_bytes < 32 * 1024


def test_dirty_submodule_transfer_is_typed_unsupported(tmp_path: Path) -> None:
    source = _repository(tmp_path)
    child_source = tmp_path / "child-source"
    child_source.mkdir()
    _git(child_source, "init", "-q", "-b", "main")
    _git(child_source, "config", "user.email", "test@example.com")
    _git(child_source, "config", "user.name", "Test")
    (child_source / "child.txt").write_text("base\n", encoding="utf-8")
    _git(child_source, "add", "child.txt")
    _git(child_source, "commit", "-qm", "child base")
    _git(
        source,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child_source),
        "child",
    )
    _git(source, "commit", "-qm", "add child")
    (source / "child" / "child.txt").write_text("dirty child\n", encoding="utf-8")

    with pytest.raises(GitUnsupportedOperationError):
        DulwichGitBackend().capture_worktree_changes(source)


def test_snapshot_manifest_is_strict_pydantic_json() -> None:
    manifest = SnapshotArtifactManifest(
        format_version=1,
        artifact_sha256="0" * 64,
        full_patch_sha256="1" * 64,
        staged_patch_sha256="2" * 64,
    )
    assert SnapshotArtifactManifest.model_validate_json(
        manifest.model_dump_json(by_alias=True)
    ) == manifest
    assert "stateSha256" not in manifest.model_dump(by_alias=True)
    with pytest.raises(ValueError):
        SnapshotArtifactManifest.model_validate_json(
            '{"formatVersion":1,"artifactSha256":"bad",'
            '"fullPatchSha256":"1111111111111111111111111111111111111111111111111111111111111111",'
            '"stagedPatchSha256":"2222222222222222222222222222222222222222222222222222222222222222"}'
        )


def test_retention_policy_is_pure_and_typed() -> None:
    candidates = (
        RetentionCandidate(
            worktree_id="old",
            managed=True,
            active=False,
            safe=True,
            idle=True,
            last_used_at=1,
        ),
        RetentionCandidate(
            worktree_id="protected",
            managed=True,
            active=True,
            safe=True,
            idle=False,
            last_used_at=0,
        ),
    )
    decision = WorktreeRetentionPolicy().select(candidates, limit=1)
    assert decision.keep == ("old", "protected")
    assert decision.cleanup == ()
    assert decision.skipped[0].worktree_id == "protected"
