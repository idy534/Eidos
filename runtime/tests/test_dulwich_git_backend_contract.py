from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from eidos_runtime.git import native
from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.models import GitWorkingTreePatch
from eidos_runtime.git.refs import GitRefValidator
from eidos_runtime.git.snapshot_artifacts import SnapshotArtifactManifest
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


def test_native_git_surface_is_reduced_to_narrow_fallbacks() -> None:
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


def test_git_ref_validator_delegates_branch_grammar_to_dulwich() -> None:
    assert GitRefValidator.branch("feature/中文") == "refs/heads/feature/中文".encode()
    for invalid in ("feature..bad", "feature@{bad}", "feature/.", "feature.lock"):
        with pytest.raises(ValueError):
            GitRefValidator.branch(invalid)


def test_capture_and_apply_preserve_binary_mode_symlink_and_index_state(
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path)
    (source / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    (source / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (source / "binary.dat").write_bytes(b"changed\x00\x01\xff\n")
    (source / "binary.dat").chmod(0o755)
    (source / "link.txt").symlink_to("tracked.txt")
    (source / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    _git(source, "add", "deleted.txt")
    (source / "deleted.txt").unlink()
    (source / "untracked.dat").write_bytes(b"untracked\x00\xff")

    backend = DulwichGitBackend()
    changes = backend.capture_worktree_changes(source)
    assert changes.full_state is not None
    assert changes.staged_state is not None
    assert GitWorkingTreePatch.model_validate_json(changes.model_dump_json()) == changes

    target = tmp_path / "target"
    backend.worktree_add(source, target, None, backend.head(source))
    backend.apply_worktree_changes(target, changes)

    assert (target / "tracked.txt").read_text(encoding="utf-8") == "unstaged\n"
    assert (target / "binary.dat").read_bytes() == b"changed\x00\x01\xff\n"
    assert (target / "binary.dat").stat().st_mode & 0o111
    assert (target / "link.txt").is_symlink()
    assert os.readlink(target / "link.txt") == "tracked.txt"
    assert not (target / "deleted.txt").exists()
    assert (target / "untracked.dat").read_bytes() == b"untracked\x00\xff"
    assert "tracked.txt" in backend.status(target).staged_paths


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
