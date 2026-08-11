from __future__ import annotations

from pathlib import Path
import subprocess

from eidos_runtime.git.process import GitProcess


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    _git(repository, "config", "user.name", "Eidos Tests")
    (repository / "README.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.txt")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _marker_executable(tmp_path: Path, marker: Path) -> Path:
    executable = tmp_path / f"marker-{marker.name}.sh"
    executable.write_text(
        "#!/bin/sh\n"
        f"printf marker >> '{marker}'\n"
        "cat\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_informational_git_observation_disables_textconv_and_filters(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    marker = tmp_path / "textconv-ran"
    (repository / ".gitattributes").write_text("*.txt diff=evil filter=evil\n")
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure filters")
    _git(repository, "config", "filter.evil.clean", "touch should-not-run")
    _git(repository, "config", "filter.evil.process", "touch should-not-run")
    _git(repository, "config", "filter.evil.required", "true")
    _git(
        repository,
        "config",
        "diff.evil.textconv",
        f"sh -c 'touch {marker}; cat \"$1\"' sh",
    )
    (repository / "README.txt").write_text("changed\n", encoding="utf-8")

    process = GitProcess()
    overrides = process._filter_config_overrides(repository)
    assert overrides == (
        "-c",
        "filter.evil.clean=",
        "-c",
        "filter.evil.process=",
        "-c",
        "filter.evil.required=false",
    )
    result = process.diff_head(repository)

    assert result.returncode == 0
    assert "changed" in result.stdout
    assert not marker.exists()


def test_dotted_executable_filter_is_disabled_for_diff_head(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = tmp_path / "dotted-filter-ran"
    executable = _marker_executable(tmp_path, marker)
    (repository / ".gitattributes").write_text(
        "*.txt filter=evil.driver\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure dotted filter")
    _git(repository, "config", "filter.evil.driver.clean", str(executable))
    (repository / "README.txt").write_text("changed\n", encoding="utf-8")

    result = GitProcess().diff_head(repository)

    assert result.returncode == 0
    assert not marker.exists()


def test_worktree_specific_executable_filter_is_disabled_for_diff_head(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    marker = tmp_path / "worktree-filter-ran"
    executable = _marker_executable(tmp_path, marker)
    (repository / ".gitattributes").write_text(
        "*.txt filter=worktree.evil\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure worktree filter")
    _git(repository, "config", "extensions.worktreeConfig", "true")
    linked = tmp_path / "linked-worktree"
    _git(repository, "worktree", "add", "-q", "-b", "linked", str(linked))
    _git(
        linked,
        "config",
        "--worktree",
        "filter.worktree.evil.clean",
        str(executable),
    )
    (linked / "README.txt").write_text("changed\n", encoding="utf-8")

    result = GitProcess().diff_head(linked)

    assert result.returncode == 0
    assert not marker.exists()


def test_status_disables_executable_fsmonitor(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    executable = _marker_executable(tmp_path, marker)
    _git(repository, "config", "core.fsmonitor", str(executable))

    result = GitProcess().status_porcelain_v2(repository)

    assert result.returncode == 0
    assert not marker.exists()


def test_git_process_environment_is_noninteractive_and_global_config_free(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    process = GitProcess()

    environment = process._execute(
        "environment-test",
        repository,
        ("rev-parse", "HEAD"),
        output_limit_bytes=4096,
    )

    assert environment.returncode == 0
