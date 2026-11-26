from __future__ import annotations

from pathlib import Path
import subprocess
import uuid

from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.git.native import GitCli, HardenedGitRunner
from eidos_runtime.protocol.methods import (
    SessionCreateRequestDto,
    SessionGitDiscardRequestDto,
    SessionGitStageHunkRequestDto,
    SessionGitStageRequestDto,
    SessionGitSuggestCommitMessageRequestDto,
    SessionGitUnstageRequestDto,
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Eidos Tests")
    _git(path, "config", "user.email", "eidos-tests@example.com")
    lines = [f"line {i}" for i in range(1, 21)]
    (path / "app.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return path


def _app(tmp_path: Path) -> tuple[SessionStore, SessionApplication]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database, managed_root=tmp_path / "worktrees")
    app = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    return store, app


def test_git_unstage_discard_hunk_and_suggest_commit_message(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    store, app = _app(tmp_path)
    try:
        session = app.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo),
                executionMode="local",
            )
        )
        session_id = session.id

        # Modify two distant hunks + add an untracked file
        lines = [f"line {i}" for i in range(1, 21)]
        lines[1] = "line 2 modified"
        lines[18] = "line 19 modified"
        (repo / "app.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (repo / "temp.txt").write_text("untracked\n", encoding="utf-8")

        # Suggest commit message from unstaged/untracked changes
        suggestion = app.git_suggest_commit_message(
            SessionGitSuggestCommitMessageRequestDto(
                sessionId=session_id,
                includeUnstaged=True,
            )
        )
        assert "app.py" in suggestion.message or "temp.txt" in suggestion.message

        # Stage and unstage a full file
        staged = app.git_stage(
            SessionGitStageRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                paths=["temp.txt"],
            )
        )
        assert "temp.txt" in staged.status.staged_files
        unstaged = app.git_unstage(
            SessionGitUnstageRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                paths=["temp.txt"],
            )
        )
        assert "temp.txt" in unstaged.status.untracked_files

        # Discard untracked file
        discarded_untracked = app.git_discard(
            SessionGitDiscardRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                paths=["temp.txt"],
            )
        )
        assert not (repo / "temp.txt").exists()
        assert "temp.txt" not in discarded_untracked.status.untracked_files

        # Stage only the first hunk via unified patch
        single_hunk_patch = "\n".join([
            "--- a/app.py",
            "+++ b/app.py",
            "@@ -2 +2 @@",
            "-line 2",
            "+line 2 modified",
            "",
        ])
        hunk_staged = app.git_stage_hunk(
            SessionGitStageHunkRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                patch=single_hunk_patch,
                reverse=False,
            )
        )
        assert "app.py" in hunk_staged.status.staged_files
        cached_diff = _git(repo, "diff", "--cached")
        assert "line 2 modified" in cached_diff
        assert "line 19 modified" not in cached_diff

        # Reverse-unstage the staged hunk
        hunk_unstaged = app.git_stage_hunk(
            SessionGitStageHunkRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                patch=single_hunk_patch,
                reverse=True,
            )
        )
        assert "app.py" not in hunk_unstaged.status.staged_files
        assert "app.py" in hunk_unstaged.status.unstaged_files

        # Discard tracked modifications
        discarded_tracked = app.git_discard(
            SessionGitDiscardRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                paths=["app.py"],
            )
        )
        assert "app.py" not in discarded_tracked.status.unstaged_files
        assert "line 2\n" in (repo / "app.py").read_text(encoding="utf-8")
    finally:
        store.close()


def test_git_unstage_works_with_unborn_head(tmp_path: Path) -> None:
    repo = tmp_path / "empty_repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "first.txt").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "first.txt")
    assert _git(repo, "status", "--porcelain").startswith("A")

    cli = GitCli(runner=HardenedGitRunner())
    cli.unstage(repo, ("first.txt",))
    assert _git(repo, "status", "--porcelain") == "?? first.txt"
