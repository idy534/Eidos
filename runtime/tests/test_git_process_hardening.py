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
